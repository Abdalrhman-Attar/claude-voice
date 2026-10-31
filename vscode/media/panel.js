// The Replies panel: recent replies from every agent as sentences to click and replay.
// Click a sentence to read from it on; its ▶ reads only that sentence.
;(function () {
  const vscode = acquireVsCodeApi()
  const VOICES = [
    'af_heart', 'af_bella', 'af_nicole', 'af_sky', 'af_sarah', 'af_nova',
    'am_michael', 'am_adam', 'am_fenrir', 'am_puck', 'bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis',
  ]
  const $ = id => document.getElementById(id)
  const saved = vscode.getState() || {}
  let state = { replies: [], status: {}, settings: {}, muted: false, ready: true }
  let selected = saved.selected || null // { id, session }; null follows the latest reply
  let lastLive = null

  const key = r => `${r.session}\u0000${r.id}`
  const current = () => {
    const list = state.replies
    if (selected) {
      const found = list.find(r => r.id === selected.id && r.session === selected.session)
      if (found) return found
    }
    return list[list.length - 1]
  }
  const select = reply => {
    const latest = state.replies[state.replies.length - 1]
    selected = !reply || reply === latest ? null : { id: reply.id, session: reply.session }
    vscode.setState({ selected })
    render(true)
  }

  function ago(t) {
    const s = Math.max(0, Date.now() / 1000 - t)
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`
    return `${Math.floor(s / 86400)}d ago`
  }

  function label(reply) {
    return reply.label || (reply.agent && reply.agent !== 'claude' ? reply.agent : 'Claude')
  }

  function renderPicker(reply) {
    const pick = $('pick')
    pick.textContent = ''
    state.replies.forEach((r, i) => {
      const o = document.createElement('option')
      o.value = String(i)
      const first = (r.sentences[0] && r.sentences[0].text) || ''
      o.textContent = `${i + 1}. ${label(r)} · ${ago(r.t)} · ${first.slice(0, 60)}`
      if (r === reply) o.selected = true
      pick.appendChild(o)
    })
    const at = state.replies.indexOf(reply)
    $('prev').disabled = at <= 0
    $('next').disabled = at < 0 || at >= state.replies.length - 1
  }

  function renderList(reply, scroll) {
    const list = $('list')
    list.textContent = ''
    if (!reply) {
      const p = document.createElement('div')
      p.className = 'empty'
      p.textContent = 'Replies from Claude Code, Codex, Gemini and Cursor show up here as they arrive.'
      list.appendChild(p)
      return
    }
    const meta = document.createElement('div')
    meta.className = 'meta'
    meta.textContent = `${label(reply)} · ${ago(reply.t)} · ${reply.sentences.length} sentences`
    list.appendChild(meta)

    const live = isLive(reply) ? state.status.index : null
    let para = null
    let box = null
    reply.sentences.forEach((s, i) => {
      if (box === null || s.para !== para) {
        box = document.createElement('div')
        box.className = 'para'
        list.appendChild(box)
        para = s.para
      }
      const row = document.createElement('div')
      row.className = 'sentence' + (live === i ? ' live' : '')
      row.dataset.index = String(i)
      const text = document.createElement('span')
      text.className = 'text'
      text.textContent = s.text
      // Shown on hover (and on the spoken row): this sentence alone, or on to the end.
      const actions = document.createElement('span')
      actions.className = 'actions'
      const one = document.createElement('button')
      one.textContent = '▶ Play'
      one.title = 'Read only this sentence'
      one.dataset.one = String(i)
      const from = document.createElement('button')
      from.textContent = '⏩ From here'
      from.title = 'Read from this sentence to the end'
      from.dataset.from = String(i)
      actions.append(one, from)
      row.append(text, actions)
      box.appendChild(row)
    })
    const liveRow = live === null ? null : list.querySelector('.sentence.live')
    if (liveRow && (scroll || live !== lastLive)) liveRow.scrollIntoView({ block: 'nearest' })
    lastLive = live
  }

  function isLive(reply) {
    const s = state.status
    return (s.state === 'playing' || s.state === 'paused') && s.ref === reply.id && s.session === reply.session
  }

  function renderState(reply) {
    const s = state.status
    const el = $('state')
    const speaking = s.state === 'playing' || s.state === 'paused'
    el.classList.toggle('on', s.state === 'playing')
    if (!speaking) el.textContent = state.muted ? 'Muted' : ''
    else if (reply && isLive(reply) && typeof s.index === 'number') {
      el.textContent = `${s.state === 'paused' ? 'Paused' : 'Speaking'} ${s.index + 1} of ${reply.sentences.length}`
    } else {
      const other = state.replies.find(r => r.id === s.ref && r.session === s.session)
      el.textContent = `${s.state === 'paused' ? 'Paused' : 'Speaking'}${other ? ` · ${label(other)}` : ''}`
    }
    $('pause').textContent = s.state === 'paused' ? '▶ Resume' : '❚❚ Pause'
  }

  function renderSettings() {
    const c = state.settings
    const voice = $('voice')
    if (!voice.options.length) {
      VOICES.forEach(v => {
        const o = document.createElement('option')
        o.value = o.textContent = v
        voice.appendChild(o)
      })
    }
    if (typeof c.voice === 'string' && !VOICES.includes(c.voice)) {
      const o = document.createElement('option')
      o.value = o.textContent = c.voice
      voice.appendChild(o)
    }
    voice.value = typeof c.voice === 'string' ? c.voice : 'af_heart'
    voice.disabled = c.projectVoices === true
    const speed = typeof c.speed === 'number' ? c.speed : 1.1
    $('speed').value = String(speed)
    $('speedOut').textContent = `${speed.toFixed(1)}×`
    $('autoSpeak').checked = !state.muted
    $('awayOnly').checked = c.awayOnly === true
    $('live').checked = c.live !== false
    $('workUpdates').checked = c.workUpdates !== false
    $('projectVoices').checked = c.projectVoices === true
    $('summarize').checked = c.summarize !== false
  }

  function render(scroll) {
    const reply = current()
    $('setup').hidden = state.ready
    $('list').hidden = !state.ready
    renderPicker(reply)
    renderList(reply, scroll)
    renderState(reply)
    renderSettings()
  }

  window.addEventListener('message', e => {
    if (e.data && e.data.type === 'state') {
      const before = current()
      state = e.data
      const after = current()
      render(!before || !after || key(before) !== key(after))
    }
  })

  $('list').addEventListener('click', e => {
    const reply = current()
    if (!reply) return
    const one = e.target.closest('[data-one]')
    if (one) {
      e.stopPropagation()
      return vscode.postMessage({ type: 'playOne', id: reply.id, session: reply.session, index: Number(one.dataset.one) })
    }
    const from = e.target.closest('[data-from]')
    if (from) {
      e.stopPropagation()
      return vscode.postMessage({ type: 'play', id: reply.id, session: reply.session, index: Number(from.dataset.from) })
    }
    const row = e.target.closest('.sentence')
    if (row) vscode.postMessage({ type: 'play', id: reply.id, session: reply.session, index: Number(row.dataset.index) })
  })
  $('pick').addEventListener('change', e => select(state.replies[Number(e.target.value)]))
  $('prev').addEventListener('click', () => select(state.replies[state.replies.indexOf(current()) - 1]))
  $('next').addEventListener('click', () => select(state.replies[state.replies.indexOf(current()) + 1]))
  $('playAll').addEventListener('click', () => {
    const reply = current()
    if (reply) vscode.postMessage({ type: 'play', id: reply.id, session: reply.session, index: 0 })
  })
  for (const id of ['pause', 'skip', 'stop']) $(id).addEventListener('click', () => vscode.postMessage({ type: id }))
  $('runSetup').addEventListener('click', () => vscode.postMessage({ type: 'setup' }))

  const setting = (k, value) => vscode.postMessage({ type: 'setting', key: k, value })
  $('voice').addEventListener('change', e => setting('voice', e.target.value))
  $('speed').addEventListener('input', e => ($('speedOut').textContent = `${Number(e.target.value).toFixed(1)}×`))
  $('speed').addEventListener('change', e => setting('speed', Math.round(Number(e.target.value) * 10) / 10))
  $('autoSpeak').addEventListener('change', e => setting('muted', !e.target.checked))
  for (const id of ['awayOnly', 'live', 'workUpdates', 'projectVoices', 'summarize']) {
    $(id).addEventListener('change', e => setting(id, e.target.checked))
  }

  // Keyboard: arrows move between replies, space pauses, escape stops.
  document.addEventListener('keydown', e => {
    if (e.target instanceof HTMLSelectElement || e.target instanceof HTMLInputElement) return
    if (e.key === 'Escape') vscode.postMessage({ type: 'stop' })
    else if (e.key === ' ') {
      e.preventDefault()
      vscode.postMessage({ type: 'pause' })
    } else if (e.key === 'ArrowLeft') $('prev').click()
    else if (e.key === 'ArrowRight') $('next').click()
  })

  setInterval(() => render(false), 30000) // keep "2m ago" current
  vscode.postMessage({ type: 'ready' })
})()
