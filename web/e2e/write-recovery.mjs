import {expect} from '@playwright/test'

export async function recoverWrite(page,pattern,scope,label,lockedControls,interruption) {
  const endpoint=new RegExp(pattern+'$')
  const isWrite=request=>request.method()==='POST'&&endpoint.test(new URL(request.url()).pathname)
  const button=name=>scope.getByRole('button',{name,exact:true})
  const envelope=request=>{
    const headers=request.headers()
    return {key:headers['idempotency-key'],revision:headers['if-match'],body:request.postData()}
  }
  let sent,committed
  const intercept=async route=>{
    const request=route.request()
    if(sent||!isWrite(request))return route.continue()
    sent=envelope(request)
    const response=await route.fetch()
    expect(response.status()).toBe(200)
    committed=(await response.json()).data
    if(interruption==='network')await route.abort()
    else await route.fulfill(interruption)
  }
  await page.route(endpoint,intercept)
  try{
    await button(label).click()
    await expect(scope.getByText(/未能确认.*按原请求重试/)).toBeVisible()
    await expect(button(label)).toBeDisabled()
    for(const control of lockedControls)await expect(control).toBeDisabled()
    const waiting=page.waitForResponse(response=>isWrite(response.request()))
    await button('重试上次提交').click()
    const response=await waiting
    expect(sent.key).toBeTruthy()
    expect(sent.revision).toBeTruthy()
    expect(envelope(response.request())).toEqual(sent)
    expect(response.status()).toBe(200)
    const {data}=await response.json()
    expect(data).toEqual(committed)
    return data
  }finally{await page.unroute(endpoint,intercept)}
}
