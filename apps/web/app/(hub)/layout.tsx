import { notFound } from 'next/navigation'
import type { ReactNode } from 'react'
import { fetchInstanceMode } from '@/lib/eeGate'

// Resolve the mode on each request so a temporary failure is never cached.
export const dynamic = 'force-dynamic'

export default async function HubLayout({ children }: { children: ReactNode }) {
  if (await fetchInstanceMode() !== 'saas') notFound()
  return <>{children}</>
}
