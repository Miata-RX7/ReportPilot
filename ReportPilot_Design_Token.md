# ReportPilot Design Token

整体风格可以概括为：**深色顶栏 + 浅灰背景 + 白卡片 + 软标签色系 + facts/insights 双色语义高亮**，接近 Linear / Vercel 的现代 SaaS 观感，但用蓝色作为主色，暖色克制。

---

## 1. 颜色 Token

### 1.1 品牌与中性色

| Token | 值 | 用途 |
|-------|-----|------|
| `--color-primary` | `#2563eb` | 主按钮、选中态、链接、facts 高亮 |
| `--color-primary-hover` | `#1d4ed8` | 悬停 |
| `--color-primary-active` | `#1e40af` | 按下 |
| `--color-primary-soft` | `#eff6ff` | 浅底（提示条、代码块） |
| `--color-accent` | `#7c3aed` | insights / AI 相关紫色强调 |
| `--color-bg` | `#f5f7fa` | 页面背景 |
| `--color-bg-elevated` | `#ffffff` | 卡片、弹窗背景 |
| `--color-bg-header` | `#1e293b` | 顶栏深色背景 |
| `--color-border` | `#e2e8f0` | 分隔线、输入框边框 |
| `--color-border-strong` | `#cbd5e1` | 强调边框（悬停） |
| `--color-text-primary` | `#0f172a` | 标题、正文主色 |
| `--color-text-secondary` | `#475569` | 副标题、表单标签 |
| `--color-text-tertiary` | `#64748b` | 辅助说明、占位符 |
| `--color-text-quaternary` | `#94a3b8` | 极淡说明、禁用态 |
| `--color-text-inverse` | `#ffffff` | 深色背景上的文字 |

### 1.2 语义色（含软标签色系）

这套系统的标签大量使用"**浅底 + 深字**"的软标签风格（soft badge），是视觉上最有辨识度的部分。

| 语义 | 文字色 | 底/边框色 | 用途 |
|------|--------|----------|------|
| success | `#047857` | `#ecfdf5` | 已上线、连接正常、已绑定、approved |
| warning | `#c2410c` | `#fff7ed` | 待处理、规划中、待绑定、pending_review |
| danger | `#b91c1c` | `#fef2f2` | 删除、rejected、错误 |
| info | `#1d4ed8` | `#eff6ff` | scalar、fact、zabbix、draft |
| accent | `#6d28d9` | `#f5f3ff` | insight、aiops、timeseries、AI 相关 |
| neutral | `#475569` | `#f1f5f9` | 已停用、已删除、灰标签 |

### 1.3 语义高亮（本项目特有）

这套系统最独特的设计语言 —— **facts 蓝 / insights 紫**贯穿全局，是"AI 不碰数字"原则的可视化。

| 语义 | 文字色 | 底/边框色 | 用途 |
|------|--------|----------|------|
| facts | `#1d4ed8` | `#eff6ff` | 指标值、{{变量}}、计算产出 |
| insights | `#6d28d9` | `#f5f3ff` | AI 摘要槽位、AI 生成文本 |

**使用规则：** 全文任何"数据 vs 文字"的对比场景，一律用这两色区分，不得使用其他色系。这是产品语义，不是装饰。

### 1.4 图表配色

折线图 / 饼图配色（Demo 截图里的折线、环形图）：

```scss
--chart-series-1: #2563eb;  // 主线（蓝）
--chart-series-2: #10b981;  // 次线（绿）
--chart-series-3: #f59e0b;  // 第三线（橙）
--chart-series-4: #7c3aed;  // 第四线（紫）
--chart-series-5: #06b6d4;  // 第五线（青）
--chart-grid:     #e2e8f0;  // 网格线
--chart-axis:     #94a3b8;  // 坐标轴文字
```

---

## 2. 字体 Token

### 2.1 字体栈

```scss
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
             "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue",
             Helvetica, Arial, sans-serif;
--font-mono: ui-monospace, "SF Mono", Menlo, Monaco, Consolas,
             "Liberation Mono", "Courier New", monospace;
```

### 2.2 字号与行高

| Token | 字号 | 行高 | 字重 | 用途 |
|-------|------|------|------|------|
| `--font-xs` | 12px | 1.5 | 400 | 辅助说明、表格脚注 |
| `--font-sm` | 13px | 1.5 | 400 | 表格正文、表单提示 |
| `--font-base` | 14px | 1.6 | 400 | 正文、表单标签、按钮 |
| `--font-md` | 16px | 1.5 | 500 | 卡片小标题 |
| `--font-lg` | 18px | 1.4 | 600 | 区块标题 |
| `--font-xl` | 20px | 1.3 | 600 | 卡片主标题 |
| `--font-2xl` | 24px | 1.3 | 600 | 页面标题 |
| `--font-3xl` | 32px | 1.2 | 700 | 数据大字号（指标值） |

---

## 3. 间距 Token

4px 基准：

```scss
--space-1:  4px;
--space-2:  8px;
--space-3:  12px;
--space-4:  16px;
--space-5:  20px;
--space-6:  24px;
--space-8:  32px;
--space-10: 40px;
--space-12: 48px;
--space-16: 64px;
```

**布局规则：**
- 卡片内边距：`--space-6` (24px)
- 卡片间距：`--space-4` (16px)
- 页面左右留白：`--space-6` 到 `--space-8`
- 表单项垂直间距：`--space-4`
- 按钮之间的间距：`--space-2` 到 `--space-3`

---

## 4. 圆角 Token

```scss
--radius-xs:   4px;   // 微标签、代码片段
--radius-sm:   6px;   // 按钮、输入框、下拉框
--radius-md:   8px;   // 卡片
--radius-lg:   12px;  // 大卡片、弹窗、报告预览"纸"
--radius-full: 999px; // 胶囊标签（UI Demo 标签、状态机节点）
```

---

## 5. 阴影 Token

这套设计的阴影**很克制**，主要靠浅灰背景和白卡片形成层次，不靠阴影堆叠。

```scss
--shadow-xs:      0 1px 2px rgba(15, 23, 42, 0.04);
--shadow-card:    0 1px 3px rgba(15, 23, 42, 0.06),
                  0 1px 2px rgba(15, 23, 42, 0.04);
--shadow-hover:   0 4px 12px rgba(15, 23, 42, 0.08);
--shadow-dropdown:0 6px 16px rgba(15, 23, 42, 0.10);
--shadow-dialog:  0 10px 30px rgba(15, 23, 42, 0.14);
--shadow-paper:   0 4px 24px rgba(15, 23, 42, 0.08); // 报告预览"纸"
```

---

## 6. 组件级 Token

```scss
/* 顶栏 */
--header-height:        60px;
--header-bg:            var(--color-bg-header);
--header-text:          #f1f5f9;
--header-tab-active-bg: var(--color-primary);
--header-tab-radius:    var(--radius-sm);

/* 卡片 */
--card-bg:              var(--color-bg-elevated);
--card-radius:          var(--radius-md);
--card-padding:         var(--space-6);
--card-shadow:          var(--shadow-card);

/* 按钮 */
--btn-height-sm:        28px;
--btn-height-md:        32px;
--btn-height-lg:        40px;
--btn-radius:           var(--radius-sm);
--btn-padding-x:        var(--space-4);

/* 输入框 */
--input-height:         32px;
--input-radius:         var(--radius-sm);
--input-border:         var(--color-border);

/* 表格 */
--table-header-bg:      #f8fafc;
--table-row-height:     48px;
--table-border:         var(--color-border);
--table-text:           var(--color-text-primary);

/* 标签（软标签） */
--tag-height:           20px;
--tag-radius:           4px;      // 或 --radius-full 视场景
--tag-padding-x:        8px;
--tag-font-size:        12px;

/* 代码块（{{变量}}、JSON 片段） */
--code-bg:              var(--color-primary-soft);
--code-text:            var(--color-primary-hover);
--code-padding:         2px 6px;
--code-radius:          var(--radius-xs);
--code-font:            var(--font-mono);

/* 报告预览"纸" */
--paper-bg:             #ffffff;
--paper-padding:        48px 56px;
--paper-radius:         var(--radius-lg);
--paper-shadow:         var(--shadow-paper);
--paper-font:           var(--font-sans);
```

---

## 7. SCSS 变量文件（可直接落地）

```scss
// frontend/src/styles/tokens.scss

:root {
  /* ---- 颜色 · 品牌与中性 ---- */
  --color-primary:        #2563eb;
  --color-primary-hover:  #1d4ed8;
  --color-primary-active: #1e40af;
  --color-primary-soft:   #eff6ff;
  --color-accent:         #7c3aed;

  --color-bg:             #f5f7fa;
  --color-bg-elevated:    #ffffff;
  --color-bg-header:      #1e293b;

  --color-border:         #e2e8f0;
  --color-border-strong:  #cbd5e1;

  --color-text-primary:     #0f172a;
  --color-text-secondary:   #475569;
  --color-text-tertiary:    #64748b;
  --color-text-quaternary:  #94a3b8;
  --color-text-inverse:     #ffffff;

  /* ---- 颜色 · 语义（软标签） ---- */
  --color-success:        #047857;
  --color-success-bg:     #ecfdf5;
  --color-warning:        #c2410c;
  --color-warning-bg:     #fff7ed;
  --color-danger:         #b91c1c;
  --color-danger-bg:      #fef2f2;
  --color-info:           #1d4ed8;
  --color-info-bg:        #eff6ff;
  --color-accent-soft:    #6d28d9;
  --color-accent-bg:      #f5f3ff;
  --color-neutral:        #475569;
  --color-neutral-bg:     #f1f5f9;

  /* ---- 颜色 · 语义高亮（facts / insights） ---- */
  --facts-color:          var(--color-info);
  --facts-bg:             var(--color-info-bg);
  --insights-color:       var(--color-accent-soft);
  --insights-bg:          var(--color-accent-bg);

  /* ---- 字体 ---- */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue",
               Helvetica, Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, Monaco, Consolas,
               "Liberation Mono", "Courier New", monospace;

  --font-xs:   12px;
  --font-sm:   13px;
  --font-base: 14px;
  --font-md:   16px;
  --font-lg:   18px;
  --font-xl:   20px;
  --font-2xl:  24px;
  --font-3xl:  32px;

  /* ---- 间距 ---- */
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-5:  20px;
  --space-6:  24px;
  --space-8:  32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;

  /* ---- 圆角 ---- */
  --radius-xs:   4px;
  --radius-sm:   6px;
  --radius-md:   8px;
  --radius-lg:   12px;
  --radius-full: 999px;

  /* ---- 阴影 ---- */
  --shadow-xs:       0 1px 2px rgba(15, 23, 42, 0.04);
  --shadow-card:     0 1px 3px rgba(15, 23, 42, 0.06),
                     0 1px 2px rgba(15, 23, 42, 0.04);
  --shadow-hover:    0 4px 12px rgba(15, 23, 42, 0.08);
  --shadow-dropdown: 0 6px 16px rgba(15, 23, 42, 0.10);
  --shadow-dialog:   0 10px 30px rgba(15, 23, 42, 0.14);
  --shadow-paper:    0 4px 24px rgba(15, 23, 42, 0.08);

  /* ---- 图表 ---- */
  --chart-series-1: #2563eb;
  --chart-series-2: #10b981;
  --chart-series-3: #f59e0b;
  --chart-series-4: #7c3aed;
  --chart-series-5: #06b6d4;
  --chart-grid:     #e2e8f0;
  --chart-axis:     #94a3b8;

  /* ---- 组件 ---- */
  --header-height: 60px;
  --card-radius:   var(--radius-md);
  --card-padding:  var(--space-6);
  --input-height:  32px;
  --btn-height:    32px;
  --table-row-h:   48px;
}
```

---

## 8. Element Plus 主题映射

Element Plus 通过 SCSS 变量覆盖：

```scss
// frontend/src/styles/element-override.scss
@use "element-plus/theme-chalk/src/common/var.scss" with (
  $colors: (
    "primary": ("base": #2563eb),
    "success": ("base": #047857),
    "warning": ("base": #c2410c),
    "danger":  ("base": #b91c1c),
    "info":    ("base": #64748b),
  ),
  $text-color: (
    "primary":   #0f172a,
    "regular":   #475569,
    "secondary": #64748b,
    "placeholder": #94a3b8,
  ),
  $border-color: (
    "":         #e2e8f0,
    "light":    #e2e8f0,
    "lighter":  #f1f5f9,
  ),
  $bg-color: (
    "":         #f5f7fa,
    "page":     #f5f7fa,
    "overlay":  #ffffff,
  ),
  $border-radius: (
    "base":     6px,
    "small":    4px,
    "round":    20px,
  ),
);
```

并在 `vite.config.ts` 里配置 `additionalData` 引入 `element-override.scss`。

---

## 9. 使用约束（给 CodeBuddy 的规则）

**必须遵守：**

1. **禁止硬编码颜色**。所有颜色只能引用 `tokens.scss` 里的变量。
2. **禁止硬编码尺寸**。字号、间距、圆角、阴影同理。
3. **facts / insights 双色不可挪作他用**。全文任何"数据 vs 文字"的对比一律用这两色；其他场景不得使用 `--facts-*` / `--insights-*`。
4. **软标签统一用「浅底 + 深字」**，不混用描边样式或纯色填充。例如"连接正常"用绿软标签，不用绿描边或不绿背景。
5. **阴影克制**。卡片默认 `--shadow-card`，不要加更多层。
6. **组件只通过 token 呈现视觉**。DataTable / FormDialog 等通用组件内部不得出现 `#xxxxxx` 或 `12px` 这类硬编码。

**后续想换配色/密度时：** 改 `tokens.scss` 一个文件即可全局生效，无需遍历组件。

---

## 10. 一句话总结

这套视觉的"高级感"来自四个约束，**不是来自复杂效果**：

1. **深顶栏 + 浅灰底 + 白卡片** 三层色阶拉出层次
2. **软标签色系**（浅底深字）统一所有状态表达
3. **facts 蓝 / insights 紫**双色语义贯穿全局
4. **阴影克制 + 圆角统一**

做到这四点，加上表格塞满数据、卡片有真实内容，观感就会立刻从"丑 Demo"变成"专业后台"。Phase 1 不需要设计师，也不需要换 UI 库。
