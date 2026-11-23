# @learnhouse/react

**Studytomy fork scope:** retained upstream package API documentation. Installing the published `@learnhouse/*` package does not include unpublished Studytomy changes. Inspect this package’s manifest, source and generated/synced output before relying on version parity. Package names and upstream attribution are intentionally retained; this documentation update does not authorize npm publication or resolve licensing decisions.

Render Studytomy Portal activities inside any React app. Pages, quizzes, flipcards, scenarios, code playgrounds, math, video and PDFs all look and behave as they do in Studytomy Portal.

The package in this checkout is compiled from the local web app and shares its version. To test the fork's renderer, use the repository workspace build. The npm command below installs the published upstream package.

```bash
npm install @learnhouse/react
```

## Usage

```tsx
import { LearnHouseActivity } from '@learnhouse/react'
import '@learnhouse/react/styles.css'

export function Lesson() {
  return (
    <LearnHouseActivity
      baseUrl="https://learn.example.com"
      activityId="activity_0b3c…"
      courseId="course_9f2a…"
    />
  )
}
```

There are four ways to say what to render:

```tsx
// By id. Passing courseId as well loads the course in parallel.
<LearnHouseActivity activityId="activity_…" courseId="course_…" />

// By name, inside a course (case-insensitive)
<LearnHouseActivity activityName="How payouts work" courseId="course_…" />

// An activity you fetched yourself, for example on the server with @learnhouse/sdk
<LearnHouseActivity activity={activity} courseId="course_…" />

// A raw document (the JSON Studytomy Portal stores), with no network calls
<LearnHouseActivity content={doc} orgId="org_…" courseId="course_…" />
```

Wrap several activities in a provider to share one client, one session and the same defaults:

```tsx
import { LearnHouseProvider, LearnHouseActivity } from '@learnhouse/react'

<LearnHouseProvider
  baseUrl="https://learn.example.com"
  getToken={() => session.accessToken}   // user token, refreshed on demand
  theme={{ accent: '#7c3aed' }}
  locale="fr"
  onEvent={(e) => analytics.track(e.type, e)}
>
  <LearnHouseActivity activityId="activity_…" />
</LearnHouseProvider>
```

Never pass an `lh_` API token to the browser. In a browser, use a user's access token. Keep API tokens on your server, together with `@learnhouse/sdk`.

## Customization

| Prop | What it does |
|---|---|
| `theme` | `mode` (`light`/`dark`/`auto`), `accent`, `background`, `text`, `mutedText`, `border`, `fontFamily`, `headingFontFamily`, `codeFontFamily`, `fontSize`, `radius`, `contentWidth`, `padding`, and `cssVariables` for anything else |
| `components` | Replace or wrap any block by node name (`blockQuiz`, `callout`, `flipcard`, `heading`, `blockImage`…). Each override gets `Default`, which renders the built-in block. |
| `marks` | Replace any mark (`link`, `bold`, `code`…) |
| `hideBlocks` | Node names to leave out |
| `transformContent` | Rewrite the document before it renders |
| `showTitle`, `header`, `footer` | The activity name, and slots above and below the content (each slot can be a function of the activity) |
| `showPoweredBy` + `siteUrl` | A small link back to the activity on your Studytomy Portal site |
| `linkTarget`, `onLinkClick` | Choose where links open, or intercept them (return `false` to cancel) |
| `locale`, `messages`, `dir` | UI language (22 bundled), string overrides keyed like Studytomy Portal's locale files, and text direction |
| `fallback`, `errorFallback` | Loading and error UI. `errorFallback` can be `(error, retry) => …` |
| `className`, `style`, `unstyled` | Hooks into the root element |

```tsx
const Callout = ({ Default, ...props }) => (
  <aside className="my-callout">
    <Default {...props} />
  </aside>
)

<LearnHouseActivity
  activityId="activity_…"
  components={{ callout: Callout }}
  hideBlocks={['blockQuiz']}
  theme={{ mode: 'auto', accent: '#0ea5e9', contentWidth: 720, radius: 8 }}
/>
```

In dark mode, text and background follow the theme. Blocks keep the light cards they use in Studytomy Portal.

## Events

`onEvent` receives `load`, `ready`, `error`, `link_click`, `media` (`play`/`pause`/`ended`/`progress` for video and audio), `resize` (useful for sizing an iframe or modal), `visible`, `complete` (the end of the activity scrolled into view, with `trackCompletion`) and `analytics` (the events Studytomy Portal's own blocks send). There are also `onLoad` and `onError` callbacks.

## Custom element

`@learnhouse/react/element` registers `<learnhouse-activity>`, the same component as a custom element for apps that don't render React (Svelte, Angular, plain HTML; Vue has [`@learnhouse/vue`](https://www.npmjs.com/package/@learnhouse/vue)). It renders in its own light DOM, so the same stylesheet applies.

```html
<script type="module">
  import '@learnhouse/react/element'
  import '@learnhouse/react/styles.css'
</script>

<learnhouse-activity base-url="https://learn.example.com" activity-id="activity_…" course-id="course_…" theme='{"accent":"#7c3aed"}'></learnhouse-activity>
```

Attributes: `activity-id`, `activity-name`, `course-id`, `base-url`, `media-url`, `site-url`, `org-id`, `token`, `locale`, `dir`, `link-target`, `theme`, `messages` and `content` (JSON), `hide-blocks` (comma list or JSON), and the booleans `show-title`, `track-completion`, `show-powered-by`, `unstyled`, `hide-error`. Objects and functions (`activity`, `content`, `client`, `getToken`, `user`, `transformContent`) are set as properties. Events arrive as DOM events: `lh-load`, `lh-ready`, `lh-error`, `lh-complete`, `lh-media`, `lh-resize`, `lh-visible`, `lh-analytics`, `lh-event` (all of them) and the cancelable `lh-link-click`, each with the event in `detail`. `el.reload()` mounts the activity again.

## Styles

`@learnhouse/react/styles.css` is Studytomy Portal's own stylesheet, compiled for the blocks the renderer uses and scoped under `.lh-activity`, so it doesn't leak into your page.

## Notes

- Blocks render without an editor: the document goes through TipTap's static renderer into Studytomy Portal's block components. A block's code loads only when a document contains that block.
- One Studytomy Portal instance per page: media URLs come from the most recently mounted provider's `baseUrl`.
- License: TODO (to be confirmed by the maintainers before the first publish).
