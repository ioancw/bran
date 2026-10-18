// fact-check — verify a document's factual claims against the web.
//
// Run by name from the orchestrator: Workflow({ name: 'fact-check', args: { path } })
// where `path` is a file the agents can Read (a briefing, a report). The
// script holds the plan; agents do the reading, searching and judging; the
// hand-offs between stages are data, not prose, so nothing gets retyped.
//
// Stages:  extract claims → verify each (in parallel) → skeptic tries to
//          refute every supported claim → compile a verdict report.

export const meta = {
  name: 'fact-check',
  description: 'Extract a document’s factual claims, verify each on the web, refute adversarially, report',
  whenToUse: 'The user asks to check, verify, audit or fact-check a document or briefing',
  phases: [
    { title: 'Extract', detail: 'one reader pulls out the checkable claims' },
    { title: 'Verify', detail: 'one verifier per claim, searching independently' },
    { title: 'Refute', detail: 'a skeptic tries to knock down each supported claim' },
    { title: 'Report', detail: 'one writer compiles the verdicts' },
  ],
}

const MAX_CLAIMS = 12
const path = typeof args === 'string' ? args : args && args.path
if (!path) throw new Error('fact-check needs args.path: the document to check')

const CLAIMS = {
  type: 'object',
  properties: {
    document_title: { type: 'string' },
    claims: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string', description: 'One checkable factual statement, self-contained' },
          source_hint: { type: 'string', description: 'The source the document gives for it, if any (domain or name)' },
          importance: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
        required: ['claim', 'importance'],
      },
    },
  },
  required: ['claims'],
}

const VERDICT = {
  type: 'object',
  properties: {
    supported: { type: 'boolean' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    evidence: { type: 'string', description: 'What you found, one or two sentences, with the specific figure or date' },
    url: { type: 'string', description: 'The single best source URL (empty string if none)' },
    correction: { type: 'string', description: 'If not supported: what the evidence actually says (empty if supported)' },
  },
  required: ['supported', 'confidence', 'evidence', 'url', 'correction'],
}

const REFUTATION = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string' },
    url: { type: 'string', description: 'Source for the refutation (empty if none)' },
  },
  required: ['refuted', 'reason', 'url'],
}

// --- Extract ---------------------------------------------------------------
phase('Extract')
const extracted = await agent(
  `Read the document at ${path} with the Read tool. List its checkable factual claims: specific figures, dates, ` +
    `named decisions, quantities, rankings — things a search could confirm or contradict. Skip opinions, forecasts ` +
    `phrased as possibilities, and the document's own structure. Return at most ${MAX_CLAIMS}, most important first, ` +
    `each self-contained (include the subject, the number and the date so it can be checked without the document).`,
  { schema: CLAIMS, label: 'extract claims' },
)
const claims = ((extracted && extracted.claims) || []).slice(0, MAX_CLAIMS)
log(`${claims.length} claims extracted from ${extracted && extracted.document_title ? extracted.document_title : path}`)
if (!claims.length) return { path, claims: [], report: 'No checkable claims were found in the document.' }

// --- Verify, then Refute — each claim flows through both stages on its own ---
const checked = await pipeline(
  claims,
  (c, _item, i) =>
    agent(
      `Verify this claim using web search (and page reads where needed): "${c.claim}". ` +
        (c.source_hint ? `The document attributes it to: ${c.source_hint}. ` : '') +
        `Find primary or authoritative sources. Decide whether the claim is supported as stated — the exact figure, ` +
        `date and direction matter. Return the verdict with the best single URL.`,
      { schema: VERDICT, phase: 'Verify', label: `verify ${i + 1}`, agentType: 'research' },
    ).then((v) => ({ claim: c, verdict: v })),
  (r, _item, i) => {
    if (!r || !r.verdict) return r
    if (!r.verdict.supported) return { ...r, refutation: null }
    return agent(
      `A verifier says this claim is supported: "${r.claim.claim}" — evidence: ${r.verdict.evidence} (${r.verdict.url || 'no URL'}). ` +
        `Try to REFUTE it: search for more recent, more authoritative or contradicting information (a later revision, ` +
        `a different official figure, a misread date). Default to refuted=false unless you find concrete contrary evidence.`,
      { schema: REFUTATION, phase: 'Refute', label: `refute ${i + 1}`, agentType: 'research' },
    ).then((ref) => ({ ...r, refutation: ref }))
  },
)

const results = checked.filter(Boolean)
const dropped = claims.length - results.length
if (dropped) log(`${dropped} claim(s) could not be checked (verifier failed)`)

// Plain code decides the outcome — no model in the loop here.
const rows = results.map((r) => {
  const v = r.verdict
  const ref = r.refutation
  const status = !v ? 'unchecked' : !v.supported ? 'not supported' : ref && ref.refuted ? 'disputed' : 'supported'
  return { claim: r.claim.claim, importance: r.claim.importance, status, verdict: v, refutation: ref }
})
const counts = rows.reduce((acc, r) => ((acc[r.status] = (acc[r.status] || 0) + 1), acc), {})
log(`verdicts: ${JSON.stringify(counts)}`)

// --- Report ----------------------------------------------------------------
phase('Report')
const report = await agent(
  `Write a fact-check report in markdown for the document at ${path}. Here are the claims and verdicts as JSON — ` +
    `use them verbatim, do not re-verify or soften them:\n\n${JSON.stringify(rows, null, 2)}\n\n` +
    `Structure: a one-paragraph summary with the counts (${JSON.stringify(counts)}); then a section per status in the order ` +
    `"Not supported", "Disputed", "Supported", "Unchecked"; each claim as a bullet with the claim, the evidence or ` +
    `correction, and the source URL as a markdown link. Mark high-importance problems with ⚠. Finish with the single most ` +
    `consequential problem, if any. Return only the markdown.`,
  { label: 'compile report', effort: 'low' },
)

return { path, counts, claims: rows, report }
