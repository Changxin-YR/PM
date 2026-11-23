export async function showNavigation(page) {
  if ((page.viewportSize()?.width ?? 1000) < 1000) {
    await page.getByRole('button', {name:'打开导航', exact:true}).click()
  }
}

export async function returnToLedger(page) {
  await page.getByRole('button', {name:'返回项目台账', exact:true}).click()
}
