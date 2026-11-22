import ExploreTopics from '@components/Studytomy/ExploreTopics'

export const dynamic = 'force-dynamic'

export default async function ExploreTopicsPage(props: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await props.params
  return <ExploreTopics key={orgslug} />
}
