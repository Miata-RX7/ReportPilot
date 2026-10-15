/** 触发浏览器下载一个 Blob（报告/预览 docx） */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** 从 Content-Disposition 解析文件名；解析失败用 fallback */
export function filenameFromDisposition(
  disposition: string | null | undefined,
  fallback: string,
): string {
  if (!disposition) return fallback
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star && star[1]) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      return fallback
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  if (plain && plain[1]) return plain[1].trim()
  return fallback
}
