import { ElMessageBox } from 'element-plus'

/**
 * 统一二次确认封装（删除 / 审核通过 / 推送等）。
 * 返回 boolean：确认 true，取消 false，调用方无需 try/catch。
 */
export async function confirmDanger(title: string, content: string): Promise<boolean> {
  try {
    await ElMessageBox.confirm(content, title, {
      type: 'warning',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    return true
  } catch {
    return false
  }
}

export function confirmDelete(name: string): Promise<boolean> {
  return confirmDanger('删除确认', `确认删除「${name}」？该操作不可撤销。`)
}
