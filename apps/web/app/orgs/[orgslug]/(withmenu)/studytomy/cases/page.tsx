import SafeguardingTriageWorkspace from '@components/Studytomy/SafeguardingTriageWorkspace'

export const metadata = {
  title: 'Safeguarding case triage',
  robots: { index: false, follow: false },
}

export default function SafeguardingCasesPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-5 p-6">
      <h1 className="text-2xl font-semibold">Safeguarding case triage</h1>
      <SafeguardingTriageWorkspace />
    </main>
  )
}
