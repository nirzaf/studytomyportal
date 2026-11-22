'use client'

import { useState } from 'react'
import { Copy, Check } from '@phosphor-icons/react/dist/ssr'
import './copy-block.css'
import './developers.css'

/** Copyable prompt and command block used by the developer guides. */
export default function CopyBlock({ label, children }) {
  const [copied, setCopied] = useState(false)
  const text = typeof children === 'string' ? children : String(children)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text.trim())
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="lhx-copy lhd-card">
      <div className="lhx-copy-bar">
        <span className="lhd-label">{label}</span>
        <button type="button" onClick={copy} className="lhx-copy-btn">
          {copied ? <Check size={13} weight="bold" /> : <Copy size={13} weight="bold" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="lhx-copy-pre">{text.trim()}</pre>
    </div>
  )
}
