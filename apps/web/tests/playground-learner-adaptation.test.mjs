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

test('generated adaptive widget updates scaffolding and challenges locally with zero backend requests', async () => {
  const dom = new Window({ url: 'http://localhost/' })
  const fetchSpy = spyOn(dom, 'fetch')
  const globalFetchSpy = spyOn(globalThis, 'fetch')

  const generatedWidgetHtml = `<!DOCTYPE html>
  <html>
    <body>
      <div id="widget">
        <div role="group" aria-label="Level selector">
          <button data-level="Foundation">Foundation</button>
          <button data-level="Extension">Extension</button>
        </div>
        <div role="group" aria-label="Focus skill selector">
          <button data-skill="Calculate mean">Calculate mean</button>
          <button data-skill="Find median">Find median</button>
        </div>
        <p id="scaffolding"></p>
        <p id="challenge"></p>
      </div>
      <script>
        const state = { level: 'Foundation', skill: 'Calculate mean' };
        const scaffoldingByLevel = {
          Foundation: 'Step-by-step worked example: add all values first, then divide by the count.',
          Extension: 'Minimal hint: check how an outlier shifts the measure.',
        };
        const challenges = {
          'Foundation|Calculate mean': 'Find the mean of 2, 4, 6.',
          'Extension|Calculate mean': 'A set of 5 numbers has mean 12. Removing one value changes the mean to 10. What was removed?',
          'Foundation|Find median': 'Order 7, 2, 5 and pick the middle value.',
          'Extension|Find median': 'Construct two different 6-number datasets with median 8.5 and range 10.',
        };
        function render() {
          document.getElementById('scaffolding').textContent = scaffoldingByLevel[state.level];
          document.getElementById('challenge').textContent = challenges[state.level + '|' + state.skill];
        }
        document.querySelectorAll('[data-level]').forEach((btn) => {
          btn.addEventListener('click', () => { state.level = btn.dataset.level; render(); });
        });
        document.querySelectorAll('[data-skill]').forEach((btn) => {
          btn.addEventListener('click', () => { state.skill = btn.dataset.skill; render(); });
        });
        render();
      </script>
    </body>
  </html>`

  try {
    dom.document.write(generatedWidgetHtml)
    dom.document.close()
    for (const script of dom.document.querySelectorAll('script')) {
      dom.eval(script.textContent || '')
    }

    const scaffolding = dom.document.getElementById('scaffolding')
    const challenge = dom.document.getElementById('challenge')

    expect(scaffolding.textContent).toContain('Step-by-step worked example')
    expect(challenge.textContent).toContain('Find the mean of 2, 4, 6.')

    // Switch level to Extension
    dom.document.querySelector('[data-level="Extension"]').click()
    expect(scaffolding.textContent).toContain('Minimal hint')
    expect(challenge.textContent).toContain('Removing one value changes the mean to 10')

    // Switch focus skill to Find median
    dom.document.querySelector('[data-skill="Find median"]').click()
    expect(challenge.textContent).toContain('Construct two different 6-number datasets')

    // Switch level back to Foundation cleanly
    dom.document.querySelector('[data-level="Foundation"]').click()
    expect(scaffolding.textContent).toContain('Step-by-step worked example')
    expect(challenge.textContent).toContain('Order 7, 2, 5')

    // Zero backend network requests on learner selection changes
    expect(fetchSpy).toHaveBeenCalledTimes(0)
    expect(globalFetchSpy).toHaveBeenCalledTimes(0)
  } finally {
    fetchSpy.mockRestore()
    globalFetchSpy.mockRestore()
    await dom.happyDOM.close()
  }
})
