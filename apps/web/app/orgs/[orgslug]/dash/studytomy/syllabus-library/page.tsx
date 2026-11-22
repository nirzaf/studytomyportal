import SyllabusLibraryManager from '@components/Studytomy/SyllabusLibraryManager'

export const dynamic = 'force-dynamic'

export default async function SyllabusLibraryPage(props: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await props.params
  return <SyllabusLibraryManager key={orgslug} />
}
