type Section = { title: string; entries: string[]; empty: string }
type Metric = { label: string; value: string }

type PublishedWeek = {
  period: string; start: string; end: string; version: number; revision: number;
  current: boolean; confirmedAt: string; projectId: string; reportId: string; contentHash: string;
  basis?: { asOf: string; ruleVersion: string; sourceHash: string; omitted: string[] };
  metrics: Metric[]; sections: Section[]; narrative?: string;
}

const entryLimit = 3

function escapeHtml(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function excerpt(value: string, limit: number) {
  const characters = Array.from(value.trim().replace(/\s+/gu, ' '))
  const shortened = characters.length > limit
  return { text: escapeHtml(characters.slice(0, limit).join('')) + (shortened ? '…' : ''), shortened }
}

function sectionHtml(section: Section) {
  const entries = section.entries.slice(0, entryLimit).map(entry => excerpt(entry, 72))
  const omitted = section.entries.length - entries.length
  const shortened = entries.filter(entry => entry.shortened).length
  const notices = []
  if (omitted) notices.push(`省略 ${omitted} 项`)
  if (shortened) notices.push(`${shortened} 项已节录`)
  const content = entries.length ? `<ul>${entries.map(entry => `<li>${entry.text}</li>`).join('')}</ul>`
    : `<p class="note">${escapeHtml(section.empty)}</p>`
  return `<section><h2>${escapeHtml(section.title)}</h2>${content}${notices.length
    ? `<p class="note">${notices.join(' · ')}，详见原报告。</p>` : ''}</section>`
}

export function weeklyReportDocument(week: PublishedWeek) {
  const title = `项目周报 ${week.period} · v${week.version}`
  const { basis } = week
  const narrative = excerpt(week.narrative ?? '', 150)
  const metrics = week.metrics.map(({ label, value }) =>
    `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>${escapeHtml(title)}</title>
  <style>
    @page { size: A4 portrait; margin: 12mm; }
    * { box-sizing: border-box; }
    body { margin: 0; color: #172437; font: 10pt/1.35 system-ui, "Microsoft YaHei", sans-serif; }
    main { overflow-wrap: anywhere; }
    header { border-bottom: 2px solid #345275; padding-bottom: 2.5mm; margin-bottom: 3mm; }
    h1 { font-size: 19pt; line-height: 1.2; margin: 0 0 1.5mm; }
    h2 { font-size: 11pt; margin: 0 0 1mm; }
    p, dl, dd { margin: 0; }
    section { margin: 2mm 0; break-inside: avoid; }
    ul { margin: 0; padding-inline-start: 5mm; }
    dt, .note { color: #40546c; }
    .metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1.5mm 3mm;
      padding: 2mm 3mm; border: 1px solid #a4b1c1; }
    .metrics dd { font-weight: 600; }
    footer { border-top: 1px solid #a4b1c1; margin-top: 3mm; padding-top: 2mm; font-size: 8pt; }
    @media screen { main { max-width: 210mm; margin: 0 auto; padding: clamp(16px, 4vw, 12mm); } }
    @media screen and (max-width: 540px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  </style>
</head>
<body><main>
  <header>
    <h1>${escapeHtml(title)}</h1>
    <p>${escapeHtml(week.start)} 至 ${escapeHtml(week.end)}（不含结束日） · 修订 ${week.revision}</p>
    <p>${week.current ? '已发布' : '历史发布版本'} · 确认时间（上海）${escapeHtml(week.confirmedAt)}</p>
  </header>
  <dl class="metrics">${metrics}</dl>
  ${week.sections.map(sectionHtml).join('')}
  ${narrative.text ? `<section><h2>补充说明</h2><p>${narrative.text}</p>${narrative.shortened
    ? '<p class="note">补充说明已节录，详见原报告。</p>' : ''}</section>` : ''}
  <footer>
    <p>项目 ID <code>${escapeHtml(week.projectId)}</code> · 报告 ID <code>${escapeHtml(week.reportId)}</code></p>
    <p>内容摘要 <code>${escapeHtml(week.contentHash)}</code></p>
    ${basis ? `<p>生成依据（上海）${escapeHtml(basis.asOf)} · 规则 ${escapeHtml(basis.ruleVersion)}</p>
    <p>依据摘要 <code>${escapeHtml(basis.sourceHash)}</code></p>` : '<p>无生成依据快照。</p>'}
    ${basis?.omitted.length ? `<p>生成时未纳入摘要：${basis.omitted.map(escapeHtml).join(' · ')}，详见原报告。</p>` : ''}
  </footer>
</main></body>
</html>`
}
