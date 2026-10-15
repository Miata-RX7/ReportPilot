import { describe, expect, it } from 'vitest'

import { filenameFromDisposition } from '@/utils/download'

describe('filenameFromDisposition', () => {
  it('解析 filename*=UTF-8', () => {
    const d = "attachment; filename*=UTF-8''preview_1_2026-08_ab12cd34.docx"
    expect(filenameFromDisposition(d, 'fallback.docx')).toBe('preview_1_2026-08_ab12cd34.docx')
  })

  it('解析带引号 filename', () => {
    expect(filenameFromDisposition('attachment; filename="report_5_2026-08.docx"', 'f.docx')).toBe(
      'report_5_2026-08.docx',
    )
  })

  it('解析不带引号 filename', () => {
    expect(filenameFromDisposition('attachment; filename=report.docx', 'f.docx')).toBe('report.docx')
  })

  it('无头或空则用 fallback', () => {
    expect(filenameFromDisposition(null, 'f.docx')).toBe('f.docx')
    expect(filenameFromDisposition('', 'f.docx')).toBe('f.docx')
    expect(filenameFromDisposition('attachment', 'f.docx')).toBe('f.docx')
  })
})
