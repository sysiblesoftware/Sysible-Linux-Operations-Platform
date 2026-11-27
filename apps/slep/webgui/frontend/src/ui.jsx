import React, { useState } from 'react'

export function Field({ label, children }) {
  return (<label className="fld">{label}{children}</label>)
}

// A simple modal with its own error line. Pass `wide` for a roomier dialog
// (e.g. the two-pane task palette).
export function Modal({ title, onClose, children, wide }) {
  return (
    <div className="modal-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className={'card modal col' + (wide ? ' wide' : '')}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        {children}
      </div>
    </div>
  )
}

export function useErr() {
  const [err, setErr] = useState('')
  const wrap = async (fn) => { setErr(''); try { await fn() } catch (e) { setErr(e.message) } }
  return { err, setErr, wrap, node: <div className="err">{err}</div> }
}

// Copy whatever is on screen. "I should be able to copy logs from ANYWHERE in the
// platforms" -- a log you can only read is a log you retype into a bug report, and
// selecting text inside a pane that is still streaming and auto-scrolling loses the
// selection every time a line arrives.
//
// `text` may be a function, so a long log is only materialised when the button is
// actually pressed rather than on every render of a streaming pane.
//
// navigator.clipboard needs a secure context; SLEP is served over TLS, but a plain
// -http deployment or an old browser would make it undefined. Falling back to a
// hidden textarea + execCommand keeps the button honest there instead of silently
// doing nothing, and the label says which happened.
export function CopyButton({ text, label = 'Copy', title = 'Copy to the clipboard', className = 'ghost sm' }) {
  const [state, setState] = React.useState('')   // '' | 'ok' | 'fail'
  const copy = async () => {
    const s = typeof text === 'function' ? text() : (text || '')
    if (!s) { setState('fail'); setTimeout(() => setState(''), 1500); return }
    let ok = false
    try {
      await navigator.clipboard.writeText(s)
      ok = true
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = s
        ta.setAttribute('readonly', '')
        ta.style.position = 'fixed'; ta.style.top = '-1000px'; ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select(); ta.setSelectionRange(0, s.length)
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      } catch { ok = false }
    }
    setState(ok ? 'ok' : 'fail')
    setTimeout(() => setState(''), 1500)
  }
  return (
    <button type="button" className={className} onClick={copy}
      title={state === 'fail' ? 'Could not reach the clipboard — select the text and copy it by hand' : title}>
      {state === 'ok' ? '✓ Copied' : state === 'fail' ? '✗ Blocked' : label}
    </button>
  )
}
