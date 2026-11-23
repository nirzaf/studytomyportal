import { expect, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import FormLayout from '../components/Objects/StyledElements/Form/Form'

test('password form can set POST in the server-rendered form before hydration', () => {
  const html = renderToStaticMarkup(
    React.createElement(FormLayout, { method: 'post', onSubmit: () => {} },
      React.createElement('input', { type: 'password', name: 'password', disabled: true })),
  )
  expect(html).toMatch(/<form\b[^>]*\bmethod="post"/i)
  expect(html).toMatch(/<input\b[^>]*\bdisabled=""/i)
})
