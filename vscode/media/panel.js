// The Replies panel: recent replies as sentences to click and replay.
// Click a sentence to read from it on; on hover, ▶ reads only that one and ⏩ reads from it.
;(function () {
  const vscode = acquireVsCodeApi()
  const VOICES = [
    'af_heart', 'af_bella', 'af_nicole', 'af_sky', 'af_sarah', 'af_nova',
    'am_michael', 'am_adam', 'am_fenrir', 'am_puck', 'bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis',
  ]
  const $ = id => document.getElementById(id)
  const saved = vscode.getState() || {}
  let state = { replies: [], status: {}, settings: {}, muted: false, ready: true }
  let selected = saved.selected || null // { id, session }; null follows the session's latest reply
  let pickedSession = saved.session || null // a session id; null follows whichever spoke last
  let open = saved.open || { voice: false, speak: false, sessions: false } // settings groups shown
  let hintDismissed = saved.hintDismissed === true
  let setupClosed = false // a finished setup's card stays until Continue
  let lastLive = null

  const save = () => vscode.setState({ selected, open, session: pickedSession, hintDismissed })
  const key = r => `${r.session}\u0000${r.id}`
  const icon = name => {
    const i = document.createElement('i')
    i.className = `codicon codicon-${name}`
    return i
  }
  // Sessions in the list, most recent activity first, each with its agent, project and span.
  function sessions() {
    const by = new Map()
    for (const r of state.replies) {
      const s = by.get(r.session) || { session: r.session, agent: r.agent || 'claude', reply: r, first: r.t, last: r.t, count: 0 }
      s.first = Math.min(s.first, r.t)
      s.last = Math.max(s.last, r.t)
      s.count++
      s.reply = r
      by.set(r.session, s)
    }
    return [...by.values()].sort((a, b) => b.last - a.last)
  }
  const sessionId = () => {
    const all = sessions()
    return (pickedSession && all.some(s => s.session === pickedSession) ? pickedSession : all[0]?.session) ?? null
  }
  const sessionReplies = () => state.replies.filter(r => r.session === sessionId())
  const current = () => {
    const list = sessionReplies()
    if (selected) {
      const found = list.find(r => r.id === selected.id && r.session === selected.session)
      if (found) return found
    }
    return list[list.length - 1]
  }
  const select = reply => {
    if (!reply) return
    const list = sessionReplies()
    const latest = list[list.length - 1]
    selected = reply === latest ? null : { id: reply.id, session: reply.session }
    save()
    render(true)
  }

  function ago(t) {
    const s = Math.max(0, Date.now() / 1000 - t)
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`
    return `${Math.floor(s / 86400)}d ago`
  }
  const agentName = r => ({ claude: 'Claude', codex: 'Codex', gemini: 'Gemini', cursor: 'Cursor' })[r.agent] || r.agent || 'Claude'
  const project = r => (r.label || '').replace(/^(Codex|Gemini|Cursor|Mcp|MCP) in /, '')
  const sessionName = r => r.title || project(r) || agentName(r)

  function when(t) {
    const d = new Date(t * 1000)
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString([], { weekday: 'short' })} ${time}`
  }
  const AGENTS = { claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI', cursor: 'Cursor' }

  // Sessions grouped by agent; two sessions in one folder are told apart by when they started.
  function renderSessions() {
    const all = sessions()
    const select = $('session')
    select.textContent = ''
    $('sessionRow').hidden = all.length < 2 && !pickedSession
    const follow = document.createElement('option')
    follow.value = ''
    const latest = all[0]
    follow.textContent = `Follow latest${latest ? ` (${sessionName(latest.reply)})` : ''}`
    select.appendChild(follow)
    const groups = new Map()
    for (const s of all) {
      const group = AGENTS[s.agent] || s.agent
      if (!groups.has(group)) {
        const g = document.createElement('optgroup')
        g.label = group
        groups.set(group, g)
        select.appendChild(g)
      }
      const o = document.createElement('option')
      o.value = s.session
      const speaking = state.status.session === s.session && (state.status.state === 'playing' || state.status.state === 'paused')
      const name = s.reply.title ? `${s.reply.title} (${project(s.reply)})` : `${project(s.reply) || agentName(s.reply)} · started ${when(s.first)}`
      o.textContent = `${speaking ? '♪ ' : ''}${name} · ${s.count} ${s.count === 1 ? 'reply' : 'replies'}`
      groups.get(group).appendChild(o)
    }
    select.value = pickedSession && all.some(s => s.session === pickedSession) ? pickedSession : ''
  }

  function renderPicker(reply) {
    const pick = $('pick')
    pick.textContent = ''
    const list = sessionReplies()
    list.forEach((r, i) => {
      const o = document.createElement('option')
      o.value = String(i)
      const first = (r.sentences[0] && r.sentences[0].text) || ''
      o.textContent = `${first.slice(0, 70)}  ·  ${ago(r.t)}`
      if (r === reply) o.selected = true
      pick.appendChild(o)
    })
    const at = list.indexOf(reply)
    $('prev').disabled = at <= 0
    $('next').disabled = at < 0 || at >= list.length - 1
    pick.disabled = list.length === 0
  }

  function isLive(reply) {
    const s = state.status
    return (s.state === 'playing' || s.state === 'paused') && s.ref === reply.id && s.session === reply.session
  }

  function renderList(reply, scroll) {
    const list = $('list')
    list.textContent = ''
    if (!reply) {
      const empty = document.createElement('div')
      empty.className = 'empty'
      const text = document.createElement('div')
      text.textContent = state.allSessions || !state.folder
        ? 'Replies from Claude Code, Codex, Gemini and Cursor show up here as they arrive.'
        : `Replies from sessions working in ${state.folder} show up here as they arrive.`
      empty.append(icon('unmute'), text)
      list.appendChild(empty)
      return
    }
    const meta = document.createElement('div')
    meta.className = 'meta'
    const badge = document.createElement('span')
    badge.className = 'badge'
    badge.textContent = agentName(reply)
    const info = document.createElement('span')
    const where = project(reply)
    info.textContent = `${reply.title ? `${reply.title} · ` : ''}${where ? `${where} · ` : ''}${ago(reply.t)} · ${reply.sentences.length} sentences · reply ${sessionReplies().indexOf(reply) + 1} of ${sessionReplies().length}`
    meta.append(badge, info)
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
      if (live === i) {
        const now = icon(state.status.state === 'paused' ? 'debug-pause' : 'unmute')
        now.classList.add('speaking')
        text.appendChild(now)
      }
      text.appendChild(document.createTextNode(s.text))
      const actions = document.createElement('span')
      actions.className = 'actions'
      const one = document.createElement('button')
      one.className = 'action'
      one.title = 'Read only this sentence'
      one.setAttribute('aria-label', 'Read only this sentence')
      one.dataset.one = String(i)
      one.appendChild(icon('play'))
      const from = document.createElement('button')
      from.className = 'action'
      from.title = 'Read from here to the end'
      from.setAttribute('aria-label', 'Read from here to the end')
      from.dataset.from = String(i)
      from.appendChild(icon('run-all'))
      actions.append(one, from)
      row.append(text, actions)
      box.appendChild(row)
    })
    const liveRow = live === null ? null : list.querySelector('.sentence.live')
    if (liveRow && (scroll || live !== lastLive)) liveRow.scrollIntoView({ block: 'nearest' })
    lastLive = live
  }

  function renderState(reply) {
    const s = state.status
    const el = $('state')
    const speaking = s.state === 'playing' || s.state === 'paused'
    el.textContent = ''
    el.classList.toggle('on', s.state === 'playing')
    if (speaking) {
      const mine = reply && isLive(reply) && typeof s.index === 'number'
      const other = state.replies.find(r => r.id === s.ref && r.session === s.session)
      el.append(icon(s.state === 'paused' ? 'debug-pause' : 'unmute'))
      el.append(
        mine
          ? `${s.index + 1} of ${reply.sentences.length}`
          : `${s.state === 'paused' ? 'Paused' : 'Speaking'}${other ? ` · ${project(other) || agentName(other)}` : ''}`,
      )
    } else if (state.muted) {
      el.append(icon('mute'), 'Muted')
    }
    const pause = $('pause')
    pause.firstElementChild.className = `codicon codicon-${s.state === 'paused' ? 'debug-continue' : 'debug-pause'}`
    pause.title = s.state === 'paused' ? 'Resume (space)' : 'Pause (space)'
    pause.classList.toggle('active', s.state === 'paused')
    for (const id of ['pause', 'skip', 'stop']) $(id).disabled = !speaking
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
    voice.title = c.projectVoices === true ? 'Each project has its own voice (Sessions)' : ''
    const speed = typeof c.speed === 'number' ? c.speed : 1.1
    $('speed').value = String(speed)
    $('speedOut').textContent = `${speed.toFixed(1)}×`
    $('autoSpeak').checked = !state.muted
    $('awayOnly').checked = c.awayOnly === true
    $('live').checked = c.live !== false
    $('workUpdates').checked = c.workUpdates !== false
    $('summarize').checked = c.summarize !== false
    $('allSessions').checked = state.allSessions === true
    $('projectVoices').checked = c.projectVoices === true

    $('voiceSummary').textContent = `${c.projectVoices === true ? 'per project' : voice.value} · ${speed.toFixed(1)}×`
    $('speakSummary').textContent = state.muted
      ? 'Muted'
      : [c.awayOnly ? 'When away' : 'Always', c.workUpdates === false ? 'final reply' : 'with updates'].join(' · ')
    $('sessionSummary').textContent = state.allSessions ? 'All sessions' : state.folder || 'This folder'
    for (const group of document.querySelectorAll('.group')) {
      const name = group.dataset.group
      const head = group.querySelector('.group-head')
      head.setAttribute('aria-expanded', String(!!open[name]))
      head.firstElementChild.className = `codicon codicon-chevron-${open[name] ? 'down' : 'right'}`
      group.querySelector('.group-body').hidden = !open[name]
    }
  }

  const STEP_COUNT = 5
  function renderSetup() {
    const s = state.setup || {}
    const finished = s.ok === true
    const stepsDone = finished ? STEP_COUNT + 1 : s.step || 0
    for (const li of document.querySelectorAll('#steps li')) {
      const n = Number(li.dataset.step)
      li.className = n < stepsDone || finished ? 'done' : n === stepsDone && s.running ? 'doing' : n === stepsDone && s.ok === false ? 'failed' : ''
    }
    const detail = $('setupDetail')
    detail.classList.toggle('error', s.ok === false)
    detail.textContent = s.ok === false ? `${s.error || 'Setup stopped.'} See the log below.` : finished ? 'All set.' : s.running ? s.detail || '' : ''
    $('runSetup').hidden = finished
    $('runSetup').disabled = !!s.running
    $('runSetup').textContent = s.running ? 'Setting up…' : s.ok === false ? 'Try again' : 'Set up'
    $('testVoice').hidden = !finished
    $('continue').hidden = !finished
    $('terminalSetup').hidden = !!s.running || finished
    $('log').textContent = (s.log || []).join('\n')
    $('logBox').hidden = !(s.log && s.log.length)
  }

  function render(scroll) {
    const reply = current()
    const s = state.setup || {}
    const setupShown = !state.ready || s.running || (s.ok !== undefined && !setupClosed)
    $('setup').hidden = !setupShown
    renderSetup()
    $('pluginHint').hidden = setupShown || state.plugin || hintDismissed
    if (setupShown) {
      $('list').hidden = true
      document.querySelector('footer').hidden = true
      document.querySelector('header').hidden = true
      return
    }
    $('setup').hidden = state.ready
    $('list').hidden = !state.ready
    document.querySelector('footer').hidden = !state.ready
    document.querySelector('header').hidden = !state.ready
    renderSessions()
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
  window.addEventListener('focus', () => document.body.classList.add('focused'))
  window.addEventListener('blur', () => document.body.classList.remove('focused'))

  const play = (type, index) => {
    const reply = current()
    if (reply) vscode.postMessage({ type, id: reply.id, session: reply.session, index })
  }
  $('list').addEventListener('click', e => {
    const one = e.target.closest('[data-one]')
    if (one) return play('playOne', Number(one.dataset.one))
    const from = e.target.closest('[data-from]')
    if (from) return play('play', Number(from.dataset.from))
    const row = e.target.closest('.sentence')
    if (row) play('play', Number(row.dataset.index))
  })
  $('session').addEventListener('change', e => {
    pickedSession = e.target.value || null
    selected = null // the session's latest reply
    save()
    render(true)
  })
  $('pick').addEventListener('change', e => select(sessionReplies()[Number(e.target.value)]))
  $('prev').addEventListener('click', () => select(sessionReplies()[sessionReplies().indexOf(current()) - 1]))
  $('next').addEventListener('click', () => select(sessionReplies()[sessionReplies().indexOf(current()) + 1]))
  $('playAll').addEventListener('click', () => play('play', 0))
  for (const id of ['pause', 'skip', 'stop']) $(id).addEventListener('click', () => vscode.postMessage({ type: id }))
  $('runSetup').addEventListener('click', () => vscode.postMessage({ type: 'setup' }))
  $('terminalSetup').addEventListener('click', () => vscode.postMessage({ type: 'terminalSetup' }))
  for (const id of ['testVoice', 'testVoice2']) $(id).addEventListener('click', () => vscode.postMessage({ type: 'test' }))
  $('installPlugin').addEventListener('click', () => vscode.postMessage({ type: 'installPlugin' }))
  $('dismissHint').addEventListener('click', () => {
    hintDismissed = true
    save()
    render(false)
  })
  $('continue').addEventListener('click', () => {
    setupClosed = true
    render(true)
  })
  for (const group of document.querySelectorAll('.group')) {
    group.querySelector('.group-head').addEventListener('click', () => {
      open = { ...open, [group.dataset.group]: !open[group.dataset.group] }
      save()
      renderSettings()
    })
  }

  const setting = (k, value) => vscode.postMessage({ type: 'setting', key: k, value })
  $('voice').addEventListener('change', e => setting('voice', e.target.value))
  $('speed').addEventListener('input', e => ($('speedOut').textContent = `${Number(e.target.value).toFixed(1)}×`))
  $('speed').addEventListener('change', e => setting('speed', Math.round(Number(e.target.value) * 10) / 10))
  $('autoSpeak').addEventListener('change', e => setting('muted', !e.target.checked))
  for (const id of ['awayOnly', 'live', 'workUpdates', 'projectVoices', 'summarize']) {
    $(id).addEventListener('change', e => setting(id, e.target.checked))
  }
  $('allSessions').addEventListener('change', e => {
    selected = null // the list changes: follow its latest reply
    pickedSession = null
    save()
    vscode.postMessage({ type: 'allSessions', value: e.target.checked })
  })

  // Keyboard: arrows move between replies, space pauses, escape stops.
  document.addEventListener('keydown', e => {
    if (e.target instanceof HTMLSelectElement || e.target instanceof HTMLInputElement) return
    if (e.key === 'Escape') vscode.postMessage({ type: 'stop' })
    else if (e.key === ' ' && !(e.target instanceof HTMLButtonElement)) {
      e.preventDefault()
      vscode.postMessage({ type: 'pause' })
    } else if (e.key === 'ArrowLeft') $('prev').click()
    else if (e.key === 'ArrowRight') $('next').click()
  })

  setInterval(() => render(false), 30000) // keep "2m ago" current
  vscode.postMessage({ type: 'ready' })
})()
