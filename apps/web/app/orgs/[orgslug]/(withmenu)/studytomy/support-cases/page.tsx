import SupportStaffWorkspace from '@components/Studytomy/SupportStaffWorkspace'

export const metadata = { title: 'Assigned support cases', robots: { index: false, follow: false } }

export default function StaffSupportCasesPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-5 p-6">
      <h1 className="text-2xl font-semibold">Assigned support cases</h1>
      <SupportStaffWorkspace />
    </main>
  )
}
