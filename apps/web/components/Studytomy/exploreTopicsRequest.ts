export type FollowUpContext = {
  sourceVersionUuids: string[]
  previousQuestion: string
}

export type ExploreTopicFilters = {
  selectedCurriculumUuid: string
  grade: string
  subject: string
  jurisdiction: string
}

export function buildExploreTopicsPayload(
  query: string,
  filters: ExploreTopicFilters,
  context: FollowUpContext | null,
) {
  const selected = filters.selectedCurriculumUuid
  return {
    query: query.trim(),
    context_question: context?.previousQuestion ?? null,
    selected_curriculum_uuid: !selected || selected === 'any' ? null : selected,
    grade: filters.grade.trim() || null,
    subject: filters.subject.trim() || null,
    jurisdiction: filters.jurisdiction.trim() || null,
    source_version_uuids: context ? [...context.sourceVersionUuids] : [],
  }
}
