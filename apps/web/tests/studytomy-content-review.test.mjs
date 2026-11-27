import { expect, test } from 'bun:test'
import { createInstance } from 'i18next'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nextProvider } from 'react-i18next'
import ContentReviewPanel from '../components/Studytomy/ContentReviewPanel'

const locale = createInstance()
await locale.init({ lng: 'en', resources: { en: { translation: {} } } })

test('course review presents separate evidence selections and requires explicit confirmation', () => {
  const markup = renderToStaticMarkup(createElement(
    I18nextProvider, { i18n: locale },
    createElement(ContentReviewPanel, {
      client: {},
      initial: {
        revision_sha256: 'a'.repeat(64),
        evidence: [
          { evidence_uuid: 'ev_rights', purpose: 'content_rights' },
          { evidence_uuid: 'ev_accessibility', purpose: 'content_accessibility' },
        ],
        next_before_id: 30,
        current_review: { uuid: 'review_fixture', revision_sha256: 'a'.repeat(64), decision: 'changes_requested' },
      },
    }),
  ))
  expect(markup).toContain('Rights evidence')
  expect(markup).toContain('Accessibility evidence')
  expect(markup).toMatch(/Rights evidence<select[^>]*>[^]*ev_rights[^]*<\/select>/)
  expect(markup).toContain('Load more attachments')
  expect(markup).toContain('Changes requested for this candidate.')
  expect(markup).toContain('Evidence purpose')
  expect(markup).toContain('Evidence file')
  expect(markup).toContain('Designate private draft for managed review')
  expect(markup).toMatch(/type="submit" disabled="">Upload attachment/)
  expect(markup).toMatch(/value="approved" disabled="">Record approval/)
  expect(markup).not.toContain('Publish course')
})
