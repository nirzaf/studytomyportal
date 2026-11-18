import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const revalidate = 0

export async function GET() {
  if (process.env.STUDYTOMY_TEST_RUNTIME_IDENTITY !== '1') {
    return NextResponse.json({ error: 'Not found' }, { status: 404 })
  }

  const buildSha = process.env.STUDYTOMY_BUILD_SHA || ''
  const sourceTreeSha256 = process.env.STUDYTOMY_BUILD_TREE_SHA256 || ''
  if (!/^[0-9a-f]{40}$/.test(buildSha) || !/^[0-9a-f]{64}$/.test(sourceTreeSha256)) {
    return NextResponse.json(
      { error: 'Runtime source identity is not configured.' },
      { status: 503, headers: { 'Cache-Control': 'private, no-store' } },
    )
  }

  return NextResponse.json(
    { build_sha: buildSha, source_tree_sha256: sourceTreeSha256 },
    { headers: { 'Cache-Control': 'private, no-store' } },
  )
}
