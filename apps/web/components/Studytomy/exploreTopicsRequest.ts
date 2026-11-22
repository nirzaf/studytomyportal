export type FollowUpContext = {
  sourceVersionUuids: string[]
  previousQuestion: string
}

export type ExploreTopicFilters = {
  selectedCurriculumUuid: string
  searchScope: 'all_approved' | 'selected_syllabus' | 'selected_subject'
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
    search_scope: filters.searchScope,
    source_version_uuids: context ? [...context.sourceVersionUuids] : [],
  }
}
