import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import PlaygroundChatPanel from '../components/Playground/PlaygroundChatPanel'
import { startPlaygroundSession, iteratePlayground } from '../services/playgrounds/generator'
import english from '../locales/en.json'

function createDomHarness() {
  const dom = new Window({ url: 'http://localhost/' })
  globalThis.window = dom
  globalThis.document = dom.document
  globalThis.HTMLElement = dom.HTMLElement
  globalThis.HTMLInputElement = dom.HTMLInputElement
  globalThis.HTMLTextAreaElement = dom.HTMLTextAreaElement
  globalThis.Event = dom.Event
  globalThis.KeyboardEvent = dom.KeyboardEvent
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  return dom
}

function setNativeValue(element, value) {
  const proto = element.tagName === 'TEXTAREA'
    ? globalThis.HTMLTextAreaElement.prototype
    : globalThis.HTMLInputElement.prototype
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'value')
  descriptor?.set?.call(element, value)
  element.dispatchEvent(new domEvent('input', { bubbles: true }))
}

function domEvent(type, init) {
  return new globalThis.Event(type, init)
}

test('PlaygroundChatPanel enables adaptation by default, edits levels/skills, preserves values on toggle, and sends expected payload', async () => {
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const sent = []
  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(PlaygroundChatPanel, {
            messages: [],
            isGenerating: false,
            iterationCount: 0,
            maxIterations: 10,
            onSend: (prompt, learnerAdaptation) => sent.push({ prompt, learnerAdaptation }),
          })
        )
      )
    })

    // 1. Default-enabled checkbox and default level labels
    const checkbox = container.querySelector('input[type="checkbox"]')
    expect(checkbox).not.toBeNull()
    expect(checkbox.checked).toBe(true)

    const getLevelInputs = () =>
      [...container.querySelectorAll('input[aria-label^="Level "]')]
    expect(getLevelInputs().map((el) => el.value)).toEqual(['Foundation', 'Core', 'Extension'])

    // 2. Rename a level, add a level, and remove a level
    await act(async () => {
      setNativeValue(getLevelInputs()[0], 'Starter')
    })
    const addLevelBtn = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Add level')
    )
    expect(addLevelBtn).toBeDefined()
    await act(async () => {
      addLevelBtn.click()
    })
    expect(getLevelInputs()).toHaveLength(4)
    await act(async () => {
      setNativeValue(getLevelInputs()[3], 'Olympiad')
    })
    const removeCoreBtn = container.querySelector('button[aria-label="Remove level Core"]')
    expect(removeCoreBtn).not.toBeNull()
    await act(async () => {
      removeCoreBtn.click()
    })
    expect(getLevelInputs().map((el) => el.value)).toEqual(['Starter', 'Extension', 'Olympiad'])

    // 3. Add focus skills via chip input
    const skillInput = container.querySelector('#playground-skills')
    const addSkillBtn = [...container.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Add'
    )
    await act(async () => {
      setNativeValue(skillInput, 'Factoring quadratics')
    })
    await act(async () => {
      addSkillBtn.click()
    })
    await act(async () => {
      setNativeValue(skillInput, 'Graphing parabolas')
    })
    await act(async () => {
      addSkillBtn.click()
    })
    expect(container.textContent).toContain('Factoring quadratics')
    expect(container.textContent).toContain('Graphing parabolas')

    // 4. Toggle off and back on without losing typed levels or skills
    await act(async () => {
      checkbox.click()
    })
    expect(checkbox.checked).toBe(false)
    expect(getLevelInputs()).toHaveLength(0)

    await act(async () => {
      checkbox.click()
    })
    expect(checkbox.checked).toBe(true)
    expect(getLevelInputs().map((el) => el.value)).toEqual(['Starter', 'Extension', 'Olympiad'])
    expect(container.textContent).toContain('Factoring quadratics')
    expect(container.textContent).toContain('Graphing parabolas')

    // 5. Send prompt and inspect payload
    const textarea = container.querySelector('textarea')
    await act(async () => {
      setNativeValue(textarea, 'Build a quadratics practice widget')
    })
    const sendBtn = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Send')
    )
    await act(async () => {
      sendBtn.click()
    })

    expect(sent).toHaveLength(1)
    expect(sent[0]).toEqual({
      prompt: 'Build a quadratics practice widget',
      learnerAdaptation: {
        enabled: true,
        levels: ['Starter', 'Extension', 'Olympiad'],
        target_skills: ['Factoring quadratics', 'Graphing parabolas'],
      },
    })
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('PlaygroundChatPanel restores saved adaptation settings when editing an existing Playground', async () => {
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(PlaygroundChatPanel, {
            messages: [],
            isGenerating: false,
            iterationCount: 1,
            maxIterations: 10,
            sessionStarted: true,
            generationError: 'The generated widget did not meet the level and skill adaptation checks.',
            initialLearnerAdaptation: {
              enabled: true,
              levels: ['Grade 10 O/L', 'Grade 11 O/L'],
              target_skills: ['Simultaneous equations'],
            },
            onSend: () => {},
          })
        )
      )
    })

    const checkbox = container.querySelector('input[type="checkbox"]')
    expect(checkbox.checked).toBe(true)
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'did not meet the level and skill adaptation checks'
    )
    const levelInputs = [...container.querySelectorAll('input[aria-label^="Level "]')]
    expect(levelInputs.map((el) => el.value)).toEqual(['Grade 10 O/L', 'Grade 11 O/L'])
    expect(container.textContent).toContain('Simultaneous equations')
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('generator client sends learner_adaptation on both start and iterate requests', async () => {
  const requests = []
  const sseBody = 'data: {"type":"chunk","content":"<div>ok</div>"}\n\ndata: {"type":"done","session_uuid":"pg_1"}\n\n'
  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response(sseBody, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  })

  try {
    const adaptation = {
      enabled: true,
      levels: ['Foundation', 'Extension'],
      target_skills: ['Calculate mean'],
    }
    await startPlaygroundSession(
      'pg_uuid_1',
      'Build stats widget',
      {
        playground_name: 'Stats',
        playground_description: 'Mean and median',
        learner_adaptation: adaptation,
      },
      'token-123',
      () => {},
      () => {},
      () => {}
    )
    await iteratePlayground(
      'pg_1',
      'pg_uuid_1',
      'Add a harder word problem',
      'token-123',
      () => {},
      () => {},
      () => {},
      '<div>ok</div>',
      { ...adaptation, enabled: false }
    )

    expect(requests).toHaveLength(2)
    expect(requests[0].url).toContain('playgrounds/generate/start')
    expect(requests[0].body.context.learner_adaptation).toEqual(adaptation)
    expect(requests[1].url).toContain('playgrounds/generate/iterate')
    expect(requests[1].body.learner_adaptation).toEqual({ ...adaptation, enabled: false })
  } finally {
    fetchSpy.mockRestore()
  }
})

test('trusted preview controls select validated level and skill variants; malformed content stays static', async () => {
  const priorGlobals = {
    window: globalThis.window,
    document: globalThis.document,
    HTMLElement: globalThis.HTMLElement,
    HTMLInputElement: globalThis.HTMLInputElement,
    HTMLTextAreaElement: globalThis.HTMLTextAreaElement,
    Event: globalThis.Event,
    KeyboardEvent: globalThis.KeyboardEvent,
    DOMParser: globalThis.DOMParser,
    isReactActEnvironment: globalThis.IS_REACT_ACT_ENVIRONMENT,
  }
  const dom = createDomHarness()
  globalThis.DOMParser = dom.DOMParser
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const SafePlaygroundFrame = (await import('../components/Playground/SafePlaygroundFrame.tsx')).default
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const savedAdaptation = {
    enabled: true,
    levels: ['Foundation', 'Extension'],
    target_skills: ['Calculate mean'],
  }

  const generatedWidgetHtml = `<!DOCTYPE html>
  <html>
    <body>
      <label>Level <select data-study-level-selector>
        <option value="Foundation">Foundation</option>
        <option value="Extension">Extension</option>
      </select></label>
      <label>Skill <select data-study-skill-selector>
        <option value="Calculate mean">Calculate mean</option>
        <option value="Find median">Find median</option>
      </select></label>
      <article data-study-adaptation-variant data-level="Foundation" data-skill="Calculate mean">
        <p data-study-scaffolding>Step-by-step worked example: add values and divide by the count.</p>
        <p data-study-challenge>Find the mean of 2, 4, 6.</p>
      </article>
      <article data-study-adaptation-variant data-level="Extension" data-skill="Calculate mean">
        <p data-study-scaffolding>Minimal hint: check how an outlier shifts the measure.</p>
        <p data-study-challenge>A set of 5 numbers has mean 12. Removing one value changes the mean to 10. What was removed?</p>
      </article>
      <article data-study-adaptation-variant data-level="Foundation" data-skill="Find median">
        <p data-study-scaffolding>Order the values first, then choose the middle position.</p>
        <p data-study-challenge>Order 7, 2, 5 and pick the middle value.</p>
      </article>
      <article data-study-adaptation-variant data-level="Extension" data-skill="Find median">
        <p data-study-scaffolding>Use the two center positions for an even-sized dataset.</p>
        <p data-study-challenge>Construct two different 6-number datasets with median 8.5 and range 10.</p>
      </article>
    </body>
  </html>`

  const readFrameDocument = () => {
    const srcdoc = container.querySelector('iframe')?.getAttribute('srcdoc') || ''
    expect(srcdoc.toLowerCase()).not.toContain('<script')
    const frameDom = new Window({ url: 'http://localhost/frame' })
    frameDom.document.write(srcdoc)
    frameDom.document.close()
    return { frameDom, document: frameDom.document }
  }

  try {
    await act(async () => {
      root.render(
        React.createElement(SafePlaygroundFrame, {
          html: generatedWidgetHtml,
          adaptation: savedAdaptation,
          title: 'Synthetic adaptive lesson',
        })
      )
    })
    await act(async () => new Promise((resolve) => setTimeout(resolve, 1)))

    const levelSelector = container.querySelector('select[aria-label="Level"]')
    const skillSelector = container.querySelector('select[aria-label="Focus skill"]')
    expect(levelSelector?.value).toBe('Foundation')
    expect(skillSelector?.value).toBe('Calculate mean')

    let frame = readFrameDocument()
    expect(frame.document.querySelectorAll('[data-study-adaptation-variant]')).toHaveLength(1)
    expect(frame.document.body.textContent).toContain('Find the mean of 2, 4, 6.')
    expect(frame.document.body.textContent).not.toContain('Construct two different')
    await frame.frameDom.happyDOM.close()

    await act(async () => {
      levelSelector.value = 'Extension'
      levelSelector.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    await act(async () => {
      skillSelector.value = 'Find median'
      skillSelector.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    frame = readFrameDocument()
    expect(frame.document.querySelectorAll('[data-study-adaptation-variant]')).toHaveLength(1)
    expect(frame.document.body.textContent).toContain('Construct two different 6-number datasets')
    await frame.frameDom.happyDOM.close()

    const malformedHtml = generatedWidgetHtml.replace(
      '<article data-study-adaptation-variant data-level="Extension" data-skill="Find median">',
      '<article data-study-adaptation-variant data-level="Unsupported" data-skill="Find median">'
    )
    await act(async () => {
      root.render(
        React.createElement(SafePlaygroundFrame, {
          html: malformedHtml,
          adaptation: savedAdaptation,
          title: 'Synthetic malformed lesson',
        })
      )
    })
    await act(async () => new Promise((resolve) => setTimeout(resolve, 1)))
    expect(container.querySelector('select[aria-label="Level"]')).toBeNull()
    expect(container.textContent).toContain('Adaptive controls are unavailable')
    frame = readFrameDocument()
    expect(frame.document.querySelectorAll('[data-study-adaptation-variant]')).toHaveLength(4)
    await frame.frameDom.happyDOM.close()
    expect(savedAdaptation).toEqual({
      enabled: true,
      levels: ['Foundation', 'Extension'],
      target_skills: ['Calculate mean'],
    })
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
    globalThis.window = priorGlobals.window
    globalThis.document = priorGlobals.document
    globalThis.HTMLElement = priorGlobals.HTMLElement
    globalThis.HTMLInputElement = priorGlobals.HTMLInputElement
    globalThis.HTMLTextAreaElement = priorGlobals.HTMLTextAreaElement
    globalThis.Event = priorGlobals.Event
    globalThis.KeyboardEvent = priorGlobals.KeyboardEvent
    globalThis.DOMParser = priorGlobals.DOMParser
    globalThis.IS_REACT_ACT_ENVIRONMENT = priorGlobals.isReactActEnvironment
  }
})

test('PlaygroundChatPanel remount (e.g. exiting fullscreen preview) preserves current parent adaptation state', async () => {
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  // Mirror PlaygroundEditor's parent state + fullscreen conditional unmount/remount
  function EditorHarness() {
    const [fullscreen, setFullscreen] = React.useState(false)
    const [adaptation, setAdaptation] = React.useState({
      enabled: true,
      levels: ['Foundation', 'Core', 'Extension'],
      target_skills: [],
    })
    return React.createElement(
      'div',
      null,
      React.createElement(
        'button',
        { type: 'button', 'data-testid': 'toggle-fs', onClick: () => setFullscreen((v) => !v) },
        fullscreen ? 'Exit fullscreen' : 'Fullscreen'
      ),
      !fullscreen &&
        React.createElement(PlaygroundChatPanel, {
          key: 'pg_fs_test',
          messages: [],
          isGenerating: false,
          iterationCount: 0,
          maxIterations: 10,
          initialLearnerAdaptation: adaptation,
          onLearnerAdaptationChange: setAdaptation,
          onSend: () => {},
        })
    )
  }

  try {
    await act(async () => {
      root.render(React.createElement(I18nextProvider, { i18n }, React.createElement(EditorHarness)))
    })

    // Rename level 1 and add a focus skill
    const levelInputs = [...container.querySelectorAll('input[aria-label^="Level "]')]
    await act(async () => {
      setNativeValue(levelInputs[0], 'Grade 10 Starter')
    })
    const skillInput = container.querySelector('#playground-skills')
    const addSkillBtn = [...container.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Add'
    )
    await act(async () => {
      setNativeValue(skillInput, 'Quadratic equations')
    })
    await act(async () => {
      addSkillBtn.click()
    })

    // Enter fullscreen (unmounts PlaygroundChatPanel) then exit fullscreen (remounts)
    const fsBtn = container.querySelector('[data-testid="toggle-fs"]')
    await act(async () => {
      fsBtn.click()
    })
    expect(container.querySelector('#playground-skills')).toBeNull()

    await act(async () => {
      fsBtn.click()
    })
    const remountedLevels = [...container.querySelectorAll('input[aria-label^="Level "]')]
    expect(remountedLevels.map((el) => el.value)).toEqual(['Grade 10 Starter', 'Core', 'Extension'])
    expect(container.textContent).toContain('Quadratic equations')
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('adaptation defaults on for new Playgrounds, preserves saved settings, and stays off for legacy widgets', async () => {
  const { learnerAdaptationForPlayground, DEFAULT_LEARNER_LEVELS } = await import(
    '../services/playgrounds/generator.ts'
  )

  expect(learnerAdaptationForPlayground({ html_content: null })).toEqual({
    enabled: true,
    levels: [...DEFAULT_LEARNER_LEVELS],
    target_skills: [],
  })
  expect(learnerAdaptationForPlayground({ html_content: '<main>Legacy widget</main>' })).toEqual({
    enabled: false,
    levels: [...DEFAULT_LEARNER_LEVELS],
    target_skills: [],
  })

  const saved = {
    html_content: '<main>Saved widget</main>',
    learner_adaptation: {
      enabled: true,
      levels: ['Grade 10 Starter', 'Core'],
      target_skills: ['Factor quadratics'],
    },
  }
  expect(learnerAdaptationForPlayground(saved)).toEqual(saved.learner_adaptation)
  expect(
    learnerAdaptationForPlayground({
      ...saved,
      learner_adaptation: { ...saved.learner_adaptation, enabled: false },
    }).enabled
  ).toBe(false)
})

test('PlaygroundPreview disables generated scripts and uses a restrictive CSP sandbox (TASK-03 / TASK-17)', async () => {
  const { default: PlaygroundPreview } = await import('../components/Playground/PlaygroundPreview.tsx')
  const {
    buildSandboxedPlaygroundHtml,
    PLAYGROUND_IFRAME_SANDBOX,
    PLAYGROUND_WIDGET_CSP,
  } = await import('../components/Playground/sandboxHtml.ts')

  const syntheticWidget = '<!DOCTYPE html><html><body><main>Widget text</main><script>placeholder</script></body></html>'

  const wrapped = buildSandboxedPlaygroundHtml(syntheticWidget)
  expect(wrapped).toContain(`<meta http-equiv="Content-Security-Policy" content="${PLAYGROUND_WIDGET_CSP}">`)
  expect(wrapped).not.toContain('<script')
  expect(PLAYGROUND_WIDGET_CSP).toContain("connect-src 'none'")
  expect(PLAYGROUND_WIDGET_CSP).toContain("form-action 'none'")
  expect(PLAYGROUND_WIDGET_CSP).toContain("frame-src 'none'")
  expect(PLAYGROUND_WIDGET_CSP).toContain("object-src 'none'")
  expect(PLAYGROUND_WIDGET_CSP).toContain("base-uri 'none'")
  expect(PLAYGROUND_WIDGET_CSP).toContain("script-src 'none'")
  expect(PLAYGROUND_IFRAME_SANDBOX).toBe('')

  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(PlaygroundPreview, { html: syntheticWidget, isStreaming: false })
        )
      )
    })
    await act(async () => new Promise((resolve) => setTimeout(resolve, 1)))

    const iframe = container.querySelector('iframe')
    expect(iframe).not.toBeNull()
    expect(iframe.hasAttribute('sandbox')).toBe(true)
    expect(iframe.getAttribute('sandbox')).toBe('')
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-forms')
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-popups')
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin')
    expect(iframe.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(iframe.srcdoc).toContain("connect-src 'none'")
    expect(iframe.srcdoc.toLowerCase()).not.toContain('<script')
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('buildSandboxedPlaygroundHtml keeps trusted policies first and generated scripts inert (T-03)', async () => {
  const { buildSandboxedPlaygroundHtml, PLAYGROUND_WIDGET_CSP } = await import(
    '../components/Playground/sandboxHtml.ts'
  )

  const syntheticDocuments = [
    '<!doctype html><!-- <head> --><html><head></head><body><div>OK</div></body></html>',
    '<!DOCTYPE html><script>placeholder</script><head></head><body><p>Hi</p></body>',
    '<html><head><title>First</title></head><head><meta http-equiv="Content-Security-Policy" content="default-src none"></head><body></body></html>',
    '<!DOCTYPE html><html><head><!-- unclosed comment streaming chunk',
    '<base/href="/relative/"><a href="#worked-example" target="_blank">Go</a><div class="ok">Fragment</div>',
    '<style>body { color: #111; }</style><svg><circle cx="10" cy="10" r="5"/></svg><script>placeholder text</script>',
  ]

  for (const fixture of syntheticDocuments) {
    const wrapped = buildSandboxedPlaygroundHtml(fixture)
    const dom = new Window({ url: 'http://localhost:3000/' })
    try {
      dom.document.write(wrapped)
      dom.document.close()

      const cspMetas = [...dom.document.querySelectorAll('meta')].filter(
        (m) => (m.getAttribute('http-equiv') || '').trim().toLowerCase() === 'content-security-policy'
      )
      expect(cspMetas.length).toBe(1)
      expect(cspMetas[0].getAttribute('content')).toBe(PLAYGROUND_WIDGET_CSP)
      expect(cspMetas[0].parentElement?.tagName.toLowerCase()).toBe('head')
      expect(dom.document.querySelector('base')).toBeNull()

      const refreshMetas = [...dom.document.querySelectorAll('meta')].filter(
        (m) => (m.getAttribute('http-equiv') || '').trim().toLowerCase() === 'refresh'
      )
      expect(refreshMetas.length).toBe(0)

      for (const link of dom.document.querySelectorAll('a, area')) {
        const href = (link.getAttribute('href') || '').trim().toLowerCase()
        expect(href.startsWith('http:') || href.startsWith('https:') || href.startsWith('//')).toBe(false)
        expect(link.getAttribute('target')).toBeNull()
      }

      // Untrusted executable markup is removed before the srcdoc is rendered.
      expect(wrapped.toLowerCase()).not.toContain('<script')
    } finally {
      await dom.happyDOM.close()
    }
  }
})

test('static Playground preview keeps lesson text and adaptation variants while removing generated execution hooks', async () => {
  const { stripUnsafeMarkup } = await import('../components/Playground/sandboxHtml.ts')
  const fixture = [
    '<p>Read the worked example.</p>',
    '<button onclick="placeholder">Continue</button>',
    '<script>placeholder content</script>',
    '<style id="studytomy-adaptive-runtime-style">[data-study-adaptation-variant]{display:none!important}</style>',
    '<article data-study-adaptation-variant data-level="Foundation" data-skill="Fractions">Guided practice</article>',
  ].join('')

  const rendered = stripUnsafeMarkup(fixture)
  expect(rendered).toContain('Read the worked example.')
  expect(rendered).toContain('Guided practice')
  expect(rendered).toContain('data-study-adaptation-variant')
  expect(rendered.toLowerCase()).not.toContain('<script')
  expect(rendered.toLowerCase()).not.toContain('onclick=')
  expect(rendered).not.toContain('studytomy-adaptive-runtime-style')
})

test('Playground navigation keeps in-document fragments and removes other destinations by default', async () => {
  const { stripUnsafeMarkup } = await import('../components/Playground/sandboxHtml.ts')
  const dom = new Window({ url: 'http://localhost:3000/' })
  const fetchSpy = spyOn(dom, 'fetch')
  const xhrOpenSpy = spyOn(dom.XMLHttpRequest.prototype, 'open')
  const xhrSendSpy = spyOn(dom.XMLHttpRequest.prototype, 'send')
  const webSocketSpy = spyOn(dom, 'WebSocket')
  const sendBeaconSpy = spyOn(dom.navigator, 'sendBeacon')
  try {
    dom.document.body.innerHTML = '<h2 id="worked-example">Worked example</h2>' + stripUnsafeMarkup(
      '<a href="#worked-example">Jump within lesson</a>' +
      '<a href="https://example.org/resource">External resource</a>' +
      '<a href="/courses/example">Portal route</a>' +
      '<a href="mailto:teacher@example.org">Email teacher</a>' +
      '<a href="#worked-example" target="_blank">New window</a>'
    )

    const links = [...dom.document.querySelectorAll('a')]
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '#worked-example',
      null,
      null,
      null,
      '#worked-example',
    ])
    expect(links.every((link) => link.getAttribute('target') === null)).toBe(true)

    links[0].click()
    expect(dom.location.hash).toBe('#worked-example')
    const fragmentUrl = dom.location.href
    for (const link of links.slice(1)) link.click()
    expect(dom.location.href).toBe(fragmentUrl)
    expect(fetchSpy).toHaveBeenCalledTimes(0)
    expect(xhrOpenSpy).toHaveBeenCalledTimes(0)
    expect(xhrSendSpy).toHaveBeenCalledTimes(0)
    expect(webSocketSpy).toHaveBeenCalledTimes(0)
    expect(sendBeaconSpy).toHaveBeenCalledTimes(0)
  } finally {
    fetchSpy.mockRestore()
    xhrOpenSpy.mockRestore()
    xhrSendSpy.mockRestore()
    webSocketSpy.mockRestore()
    sendBeaconSpy.mockRestore()
    await dom.happyDOM.close()
  }
})

test('startPlaygroundSession propagates terminal SSE error without firing onComplete and detects unexpected EOF (T-08)', async () => {
  const { startPlaygroundSession } = await import('../services/playgrounds/generator.ts')
  const encoder = new TextEncoder()

  const buildSseResponse = (chunks) =>
    new Response(
      new ReadableStream({
        start(controller) {
          for (const ch of chunks) controller.enqueue(encoder.encode(ch))
          controller.close()
        },
      }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
    )

  let callCount = 0
  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async () => {
    callCount += 1
    if (callCount === 1) {
      // Partial chunk followed by error, and even a trailing done frame that must be ignored
      return buildSseResponse([
        `data: ${JSON.stringify({ type: 'chunk', content: '<!DOCTYPE html><html>' })}\n\n`,
        `data: ${JSON.stringify({ type: 'error', message: 'An internal error occurred.' })}\n\n`,
        `data: ${JSON.stringify({ type: 'done', session_uuid: 'pg_should_ignore' })}\n\n`,
      ])
    }
    // Truncated stream with no done or error frame
    return buildSseResponse([
      `data: ${JSON.stringify({ type: 'chunk', content: '<!DOCTYPE html>' })}\n\n`,
    ])
  })

  try {
    const events1 = []
    await startPlaygroundSession(
      'pg_1',
      'Make a lab',
      {
        playground_name: 'Lab',
        playground_description: 'Desc',
        learner_adaptation: { enabled: true, levels: ['Foundation', 'Core'], target_skills: [] },
      },
      'tok',
      (c) => events1.push(['chunk', c]),
      (s) => events1.push(['done', s]),
      (e) => events1.push(['error', e])
    )
    expect(events1).toEqual([
      ['chunk', '<!DOCTYPE html><html>'],
      ['error', 'An internal error occurred.'],
    ])

    const events2 = []
    await startPlaygroundSession(
      'pg_1',
      'Make a lab',
      {
        playground_name: 'Lab',
        playground_description: 'Desc',
        learner_adaptation: { enabled: false, levels: [], target_skills: [] },
      },
      'tok',
      (c) => events2.push(['chunk', c]),
      (s) => events2.push(['done', s]),
      (e) => events2.push(['error', e])
    )
    expect(events2).toEqual([
      ['chunk', '<!DOCTYPE html>'],
      ['error', 'Generation stream ended before completion.'],
    ])
  } finally {
    fetchSpy.mockRestore()
  }
})

test('submitPlaygroundReviewDecision sends expected digest, Idempotency-Key, and surfaces stale 409 error (T-05)', async () => {
  const {
    getPlaygroundReviewStatus,
    submitPlaygroundReviewDecision,
    PlaygroundReviewError,
  } = await import('../services/playgrounds/playgrounds.ts')

  const calls = []
  let mode = 'ok'
  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const href = String(url)
    const method = (init?.method || 'GET').toUpperCase()
    calls.push({ href, method, headers: init?.headers, body: init?.body })
    if (method === 'GET' && href.includes('/content-review-status')) {
      return new Response(
        JSON.stringify({
          playground_uuid: 'pg_gov_1',
          governed: true,
          published: false,
          deliverable: false,
          content_digest: 'a'.repeat(64),
          latest_decision: null,
          latest_reviewed_digest: null,
          current_revision_approved: false,
          can_review: true,
          is_creator: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (method === 'POST' && href.includes('/content-review-decisions')) {
      if (mode === 'stale') {
        return new Response(
          JSON.stringify({
            detail: { code: 'PLAYGROUND_REVIEW_STALE', message: 'Playground changed since inspection.' },
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } }
        )
      }
      return new Response(
        JSON.stringify({
          review_event_uuid: 'rev_pg_1',
          playground_uuid: 'pg_gov_1',
          decision: 'approved',
          content_digest: 'a'.repeat(64),
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    }
    return new Response(JSON.stringify({}), { status: 404 })
  })

  try {
    const status = await getPlaygroundReviewStatus('org_lk', 'pg_gov_1', 'tok_rev')
    expect(status.governed).toBe(true)
    expect(status.can_review).toBe(true)

    const receipt = await submitPlaygroundReviewDecision(
      'org_lk',
      'pg_gov_1',
      {
        expected_digest: status.content_digest,
        decision: 'approved',
        rights_confirmed: true,
        accessibility_confirmed: true,
        age_suitability_confirmed: true,
        evidence_reference: 'REVIEW-LK-PG-99',
      },
      'tok_rev',
      'idem-ui-pg-1'
    )
    expect(receipt.decision).toBe('approved')
    const postCall = calls.find((c) => c.method === 'POST')
    expect(new Headers(postCall.headers).get('Idempotency-Key')).toBe('idem-ui-pg-1')

    mode = 'stale'
    let caught = null
    try {
      await submitPlaygroundReviewDecision(
        'org_lk',
        'pg_gov_1',
        {
          expected_digest: status.content_digest,
          decision: 'rejected',
          rights_confirmed: false,
          accessibility_confirmed: false,
          age_suitability_confirmed: false,
          evidence_reference: 'REJECT-STALE',
        },
        'tok_rev',
        'idem-ui-pg-2'
      )
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(PlaygroundReviewError)
    expect(caught.status).toBe(409)
    expect(caught.code).toBe('PLAYGROUND_REVIEW_STALE')
  } finally {
    fetchSpy.mockRestore()
  }
})
