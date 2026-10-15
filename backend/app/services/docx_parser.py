"""Word 模板变量解析与 Jinja 语法安全扫描。

安全约束：docxtpl 底层是 Jinja2，语法能力过强，上传时即收紧为
"仅允许 {{ var }} 简单变量"（字母/下划线开头的合法标识符），
其余一切 {{ }} / {% %} 片段一律拒绝。

跨 run 变量处理：Word 会把 {{var}} 拆进多个 <w:t>，
本模块按 <w:p> 段落拼接所有 <w:t> 文本后再扫描，天然还原。
"""

import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

# wordprocessingml 主命名空间（<w:p>/<w:t> 必须带命名空间匹配）
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{W_NS}}}p"
W_T = f"{{{W_NS}}}t"

# 正文 + 页眉 + 页脚
XML_PART_RE = re.compile(r"^word/(document|header\d*|footer\d*)\.xml$")
# 合法简单变量：{{ var }}
VAR_RE = re.compile(r"^\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}$")
# 疑似 Jinja 片段（{{ ... }} 或 {% ... %}）
SUSPECT_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")
JINJA_DELIMITERS = ("{{", "}}", "{%", "%}")

MAX_VIOLATIONS = 20


@dataclass(frozen=True)
class PlaceholderInfo:
    """模板变量及其在文档中的位置特征。

    is_alone_in_paragraph：该变量是否**独占一个段落**（去掉首尾空白后整段
    恰好就是 `{{ name }}`）。docxtpl 的 InlineImage 要求这一点，否则会把
    140mm 宽的行内图片硬塞进文字中间，版式不可控。
    """

    name: str
    is_alone_in_paragraph: bool


@dataclass
class TemplatePlaceholders:
    """parse_template 的返回值。

    names 保持文档出现顺序（去重）；alone 给出每个变量是否独占段落，
    多次出现时取 AND（任一次混排即视为不独占）。
    """

    names: list[str] = field(default_factory=list)
    alone: dict[str, bool] = field(default_factory=dict)

    def as_infos(self) -> list[PlaceholderInfo]:
        return [
            PlaceholderInfo(name=n, is_alone_in_paragraph=self.alone.get(n, False))
            for n in self.names
        ]


class TemplateSyntaxError(Exception):
    """模板含非法语法。violations 为违规片段描述列表。"""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        preview = "; ".join(violations[:5])
        super().__init__(f"模板包含不支持的语法: {preview}")


def _iter_paragraph_texts(xml_bytes: bytes) -> list[str]:
    """按段落拼接 <w:t> 文本（跨 run 变量在此还原）。"""
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for p in root.iter(W_P):
        text = "".join(t.text or "" for t in p.iter(W_T))
        if text:
            paragraphs.append(text)
    return paragraphs


def _scan_text(
    text: str, part: str, violations: list[str]
) -> list[tuple[str, bool]]:
    """扫描单段文本，返回本段合法变量 [(name, alone_in_paragraph)]。

    alone 判定：整段去掉首尾空白后恰好是一个合法变量 -> True；与其它文字
    混排 -> False。先按"独占"判定，因此混排的多个变量不会互相覆盖。
    """
    stripped = text.strip()
    sole = VAR_RE.match(stripped)
    if sole:
        # 首尾空白不影响 docxtpl 插图（实测），仍算独占
        return [(sole.group(1), True)]

    found: list[tuple[str, bool]] = []
    residual = text
    for m in SUSPECT_RE.finditer(text):
        fragment = m.group(0)
        var_match = VAR_RE.match(fragment)
        if var_match:
            found.append((var_match.group(1), False))
            residual = residual.replace(fragment, "", 1)
        else:
            violations.append(f"[{part}] 非法片段: {fragment!r}")
    # 兜底：合法变量移除后仍残留 Jinja 定界符（未闭合、嵌套、孤立等）
    for token in JINJA_DELIMITERS:
        if token in residual:
            violations.append(
                f"[{part}] 未闭合或非法定界符 {token!r}，所在文本: {text!r}"
            )
            break
    return found


def parse_template(file_path: Path) -> TemplatePlaceholders:
    """解析模板变量与独占段落信息。

    返回 TemplatePlaceholders（names 保持文档出现顺序去重，alone 为独占标记）；
    存在任何语法违规时抛 TemplateSyntaxError（不落库）。
    """
    violations: list[str] = []
    result = TemplatePlaceholders()
    seen: set[str] = set()
    with zipfile.ZipFile(file_path) as zf:
        # sorted 保证 document.xml 先于 footer/header（字母序 d<f<h），顺序稳定
        for name in sorted(zf.namelist()):
            if not XML_PART_RE.match(name):
                continue
            try:
                paragraphs = _iter_paragraph_texts(zf.read(name))
            except ET.ParseError:
                logger.warning("模板 part XML 解析失败，跳过: %s", name)
                continue
            for text in paragraphs:
                for var, alone in _scan_text(text, name, violations):
                    if var not in seen:
                        seen.add(var)
                        result.names.append(var)
                        result.alone[var] = alone
                    elif not alone:
                        # 多次出现取 AND：任一次混排即不可用于图表
                        result.alone[var] = False
    if violations:
        raise TemplateSyntaxError(violations[:MAX_VIOLATIONS])
    return result


def placeholder_alone_map(version: Any, names: list[str]) -> dict[str, bool]:
    """按需解析某模板版本里各变量是否独占段落。

    刻意**不落库**：独占信息只能从 docx 文件读出，存进库会产生"文件已改、
    元数据未更新"的漂移风险；模板详情/绑定保存都不是热点路径，多读一次
    zip（约几十毫秒）换来实现简单与数据可信。

    文件缺失或解析失败时返回空 dict（调用方按"非独占"处理，图表绑定会被
    拒，符合"不确定时从严"的取向）。
    """
    from pathlib import Path as _Path

    path_str = getattr(version, "file_path", "") or ""
    if not path_str:
        return {}
    try:
        parsed = parse_template(_Path(path_str))
    except (OSError, TemplateSyntaxError) as exc:
        logger.warning("独占段落解析失败，回退为非独占 file=%s: %s", path_str, exc)
        return {}
    return {n: parsed.alone.get(n, False) for n in names}
