// ==UserScript==
// @name         Notify Hub 公众号导入助手
// @namespace    notify-hub
// @version      0.2.0
// @description  从 Notify Hub 文章库把 AI 生成的文章一键填入公众号编辑器；发布按钮不自动点击，最终发布由人工确认。
// @author       Notify Hub
// @match        https://mp.weixin.qq.com/*
// @icon         https://mp.weixin.qq.com/favicon.ico
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_addStyle
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict'

  const STORE_BASE = 'nhmp_base'
  const STORE_TOKEN = 'nhmp_token'
  const DEFAULT_BASE = 'http://localhost:8000'
  const PAGE_SIZE = 20
  const STATE_KEY = 'nhmp_panel_open'
  const POS_KEY = 'nhmp_panel_pos'

  let base = GM_getValue(STORE_BASE, DEFAULT_BASE)
  let token = GM_getValue(STORE_TOKEN, '')
  let panel = null
  let listBox = null
  let statusBox = null
  let articles = []

  function fmt(url) {
    return String(url || '').replace(/\/+$/, '')
  }

  function setConfig(key, value) {
    GM_setValue(key, value)
  }

  function toast(message, tone) {
    if (!panel || !statusBox) return
    statusBox.textContent = message
    statusBox.className = 'nhmp-status ' + (tone || 'info')
    clearTimeout(toast._timer)
    if (tone) {
      toast._timer = setTimeout(function () {
        statusBox.textContent = ''
        statusBox.className = 'nhmp-status'
      }, 4000)
    }
  }

  function request(method, path) {
    return new Promise(function (resolve, reject) {
      if (!token) {
        reject(new Error('未配置访问令牌：请在脚本菜单里设置'))
        return
      }
      GM_xmlhttpRequest({
        method: method,
        url: fmt(base) + path,
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer ' + token,
        },
        timeout: 15000,
        onload: function (response) {
          var body = null
          try {
            body = JSON.parse(response.responseText)
          } catch (_) {
            reject(new Error('响应不是 JSON（HTTP ' + response.status + '）'))
            return
          }
          if (response.status >= 400 || (body && body.error)) {
            var message = (body && body.error && body.error.message) || '请求失败（HTTP ' + response.status + '）'
            reject(new Error(message))
            return
          }
          resolve(body ? body.data : null)
        },
        onerror: function () {
          reject(new Error('网络请求失败，请检查 Notify-Hub 地址'))
        },
        ontimeout: function () {
          reject(new Error('请求超时'))
        },
      })
    })
  }

  function loadArticles() {
    if (!token) {
      toast('请先在脚本菜单设置访问令牌', 'warn')
      return
    }
    toast('加载中…', 'info')
    request('GET', '/api/v1/admin/articles?status=ready&page_size=' + PAGE_SIZE)
      .then(function (data) {
        articles = (data && Array.isArray(data.items) ? data.items : []).filter(function (item) {
          return item && item.status === 'ready'
        })
        renderList()
        toast(articles.length ? '找到 ' + articles.length + ' 篇待发布文章' : '没有待发布文章', 'ok')
      })
      .catch(function (error) {
        articles = []
        renderList()
        toast(error.message, 'err')
      })
  }

  function renderList() {
    if (!listBox) return
    listBox.innerHTML = ''
    if (!articles.length) {
      var empty = document.createElement('div')
      empty.className = 'nhmp-empty'
      empty.textContent = '暂无待发布文章'
      listBox.appendChild(empty)
      return
    }
    articles.forEach(function (article) {
      var row = document.createElement('div')
      row.className = 'nhmp-row'

      var main = document.createElement('div')
      main.className = 'nhmp-row-main'
      var title = document.createElement('div')
      title.className = 'nhmp-row-title'
      title.textContent = article.title || '(无标题)'
      var meta = document.createElement('div')
      meta.className = 'nhmp-row-meta'
      meta.textContent = [
        article.author ? '作者 ' + article.author : '',
        article.ai_status ? 'AI ' + article.ai_status : '',
        article.created_at ? new Date(article.created_at).toLocaleString('zh-CN') : '',
      ].filter(Boolean).join(' · ')
      main.appendChild(title)
      main.appendChild(meta)
      row.appendChild(main)

      var actions = document.createElement('div')
      actions.className = 'nhmp-row-actions'

      var fill = document.createElement('button')
      fill.type = 'button'
      fill.className = 'nhmp-btn nhmp-btn-primary'
      fill.textContent = '填入编辑器'
      fill.addEventListener('click', function () {
        fillArticle(article)
      })
      actions.appendChild(fill)

      var copy = document.createElement('button')
      copy.type = 'button'
      copy.className = 'nhmp-btn'
      copy.textContent = '复制正文'
      copy.addEventListener('click', function () {
        copyArticle(article)
      })
      actions.appendChild(copy)

      row.appendChild(actions)
      listBox.appendChild(row)
    })
  }

  function setNativeValue(element, value) {
    var proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
    var setter = Object.getOwnPropertyDescriptor(proto, 'value')
    if (setter && setter.set) setter.set.call(element, value)
    else element.value = value
    element.dispatchEvent(new Event('input', { bubbles: true }))
    element.dispatchEvent(new Event('change', { bubbles: true }))
  }

  function findTitle() {
    return document.querySelector('#title') ||
      document.querySelector('input[id*="title" i]') ||
      document.querySelector('input[placeholder*="标题" i]')
  }

  function findAuthor() {
    return document.querySelector('#author') ||
      document.querySelector('input[id*="author" i]') ||
      document.querySelector('input[placeholder*="作者" i]')
  }

  function findSummary() {
    return document.querySelector('#js_summary') ||
      document.querySelector('textarea[id*="summary" i]') ||
      document.querySelector('input[id*="summary" i]') ||
      document.querySelector('textarea[placeholder*="摘要" i]')
  }

  function findEditor() {
    return document.querySelector('#js_editor_content') ||
      document.querySelector('div[contenteditable="true"][id*="editor" i]') ||
      document.querySelector('.js_editor_content')
  }

  function fillArticle(article) {
    var filledNames = []
    var title = findTitle()
    if (title) {
      setNativeValue(title, article.title || '')
      filledNames.push('标题')
    }
    var author = findAuthor()
    if (author) {
      setNativeValue(author, article.author || '')
      filledNames.push('作者')
    }
    var summary = findSummary()
    if (summary) {
      setNativeValue(summary, article.digest || '')
      filledNames.push('摘要')
    }
    var editor = findEditor()
    if (editor) {
      editor.focus()
      editor.innerHTML = article.content_html || ''
      editor.dispatchEvent(new Event('input', { bubbles: true }))
      filledNames.push('正文')
    }
    var filled = ['标题', '作者', '摘要', '正文'].filter(function (name) {
      return filledNames.indexOf(name) === -1
    })
    var note = filled.length ? '已填入：' + filled.join('、') : '未找到编辑器字段'
    if (article.cover_url) {
      note += '；封面请点“从正文选择”使用首图（' + article.cover_url + '）'
    }
    toast(note, 'ok')
  }

  function copyArticle(article) {
    var html = article.content_html || ''
    var text = article.content || ''
    if (navigator.clipboard && window.ClipboardItem) {
      navigator.clipboard.write([
        new window.ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        }),
      ]).then(function () {
        toast('正文已复制（富文本），Ctrl+V 即可粘贴', 'ok')
      }).catch(function () {
        toast('复制失败，请手动复制', 'err')
      })
      return
    }
    var holder = document.createElement('div')
    holder.contentEditable = 'true'
    holder.style.cssText = 'position:fixed;left:-9999px;top:0;'
    holder.innerHTML = html
    document.body.appendChild(holder)
    var range = document.createRange()
    range.selectNodeContents(holder)
    var selection = window.getSelection()
    selection.removeAllRanges()
    selection.addRange(range)
    var ok = document.execCommand('copy')
    holder.remove()
    toast(ok ? '正文已复制（富文本）' : '复制失败，请手动复制', ok ? 'ok' : 'err')
  }

  function makeDraggable(el, handle) {
    handle.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return
      if (e.target.tagName === 'BUTTON' || (e.target.closest && e.target.closest('button'))) return

      var rect = el.getBoundingClientRect()
      var startX = e.clientX
      var startY = e.clientY
      var initialTop = rect.top
      var initialLeft = rect.left
      var dragged = false

      function onMouseMove(me) {
        var dx = me.clientX - startX
        var dy = me.clientY - startY
        if (!dragged && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
          dragged = true
        }
        if (dragged) {
          me.preventDefault()
          var top = Math.max(8, Math.min(window.innerHeight - 44, initialTop + dy))
          var left = Math.max(8, Math.min(window.innerWidth - rect.width - 8, initialLeft + dx))
          el.style.top = top + 'px'
          el.style.left = left + 'px'
          el.style.right = 'auto'
        }
      }

      function onMouseUp() {
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
        if (dragged) {
          var finalRect = el.getBoundingClientRect()
          var centerX = finalRect.left + finalRect.width / 2
          var topStr = el.style.top

          if (centerX > window.innerWidth / 2) {
            // Anchor to right edge so panel expands inwards leftward without overflowing
            var rightVal = Math.max(12, Math.min(window.innerWidth - 60, window.innerWidth - finalRect.right))
            el.style.right = rightVal + 'px'
            el.style.left = 'auto'
            GM_setValue(POS_KEY, JSON.stringify({
              top: topStr,
              right: el.style.right,
              anchor: 'right',
            }))
          } else {
            // Anchor to left edge
            var leftVal = Math.max(12, finalRect.left)
            el.style.left = leftVal + 'px'
            el.style.right = 'auto'
            GM_setValue(POS_KEY, JSON.stringify({
              top: topStr,
              left: el.style.left,
              anchor: 'left',
            }))
          }
        }
      }

      document.addEventListener('mousemove', onMouseMove)
      document.addEventListener('mouseup', onMouseUp)
    })
  }

  function buildPanel() {
    if (panel) return
    panel = document.createElement('div')
    panel.className = 'nhmp-panel'
    panel.id = 'nhmp-panel'

    var header = document.createElement('div')
    header.className = 'nhmp-header'

    var grip = document.createElement('span')
    grip.className = 'nhmp-grip'
    grip.innerHTML = '⋮⋮'

    var title = document.createElement('span')
    title.className = 'nhmp-title'
    title.textContent = 'Notify-Hub 导入'

    var close = document.createElement('button')
    close.type = 'button'
    close.className = 'nhmp-btn nhmp-btn-icon nhmp-toggle-btn'
    close.textContent = '收起'

    function updateToggleText() {
      var isCollapsed = panel.classList.contains('nhmp-collapsed')
      close.textContent = isCollapsed ? '展开' : '收起'
    }

    function togglePanel() {
      panel.classList.toggle('nhmp-collapsed')
      var isCollapsed = panel.classList.contains('nhmp-collapsed')
      GM_setValue(STATE_KEY, isCollapsed ? '1' : '0')
      updateToggleText()
    }

    close.addEventListener('click', function (e) {
      e.stopPropagation()
      togglePanel()
    })

    header.addEventListener('click', function (e) {
      if (panel.classList.contains('nhmp-collapsed')) {
        if (e.target.tagName === 'BUTTON' || (e.target.closest && e.target.closest('button'))) return
        togglePanel()
      }
    })

    header.appendChild(grip)
    header.appendChild(title)
    header.appendChild(close)
    panel.appendChild(header)

    statusBox = document.createElement('div')
    statusBox.className = 'nhmp-status'
    panel.appendChild(statusBox)

    var toolbar = document.createElement('div')
    toolbar.className = 'nhmp-toolbar'
    var refresh = document.createElement('button')
    refresh.type = 'button'
    refresh.className = 'nhmp-btn nhmp-btn-primary'
    refresh.textContent = '刷新列表'
    refresh.addEventListener('click', loadArticles)
    toolbar.appendChild(refresh)
    panel.appendChild(toolbar)

    listBox = document.createElement('div')
    listBox.className = 'nhmp-list'
    panel.appendChild(listBox)

    var footer = document.createElement('div')
    footer.className = 'nhmp-footer'
    footer.textContent = '发布按钮不会被自动点击；导入后请人工检查并发布。'
    panel.appendChild(footer)

    var posRaw = GM_getValue(POS_KEY, '')
    if (posRaw) {
      try {
        var pos = JSON.parse(posRaw)
        if (pos.top) panel.style.top = pos.top
        if (pos.anchor === 'left' && pos.left) {
          panel.style.left = pos.left
          panel.style.right = 'auto'
        } else if (pos.right) {
          panel.style.right = pos.right
          panel.style.left = 'auto'
        }
      } catch (_) {}
    } else {
      panel.style.right = '16px'
      panel.style.top = '25%'
      panel.style.left = 'auto'
    }

    makeDraggable(panel, header)

    document.body.appendChild(panel)
    if (GM_getValue(STATE_KEY, '0') === '1') panel.classList.add('nhmp-collapsed')
    updateToggleText()
  }

  function onEditorPage() {
    var path = location.pathname || ''
    var search = location.search || ''
    return path.indexOf('/cgi-bin/appmsg') !== -1 && search.indexOf('t=media/appmsg_edit') !== -1
  }

  function boot() {
    GM_addStyle(
      '#nhmp-panel{position:fixed;right:16px;top:25%;z-index:2147483646;width:340px;max-height:75vh;display:flex;flex-direction:column;background:#ffffff;border:1px solid rgba(0,0,0,.08);border-radius:12px;box-shadow:0 12px 36px rgba(0,0,0,.16),0 2px 8px rgba(0,0,0,.06);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1f1f1f;font-size:13px;line-height:1.5;overflow:hidden;transition:box-shadow .2s ease,border-radius .2s ease}' +
      '#nhmp-panel.nhmp-collapsed{width:auto;max-height:40px;border-radius:20px;border-color:transparent;box-shadow:0 4px 16px rgba(7,193,96,.38),0 2px 6px rgba(0,0,0,.12);cursor:pointer}' +
      '#nhmp-panel.nhmp-collapsed:hover{box-shadow:0 6px 20px rgba(7,193,96,.48),0 2px 8px rgba(0,0,0,.16);transform:translateY(-1px)}' +
      '#nhmp-panel.nhmp-collapsed .nhmp-header{padding:6px 14px;gap:10px;border-radius:20px;background:linear-gradient(135deg,#07c160 0%,#05a04e 100%)}' +
      '#nhmp-panel.nhmp-collapsed .nhmp-grip{display:none}' +
      '#nhmp-panel.nhmp-collapsed .nhmp-list,#nhmp-panel.nhmp-collapsed .nhmp-toolbar,#nhmp-panel.nhmp-collapsed .nhmp-status,#nhmp-panel.nhmp-collapsed .nhmp-footer{display:none}' +
      '.nhmp-header{display:flex;align-items:center;gap:8px;padding:10px 14px;background:linear-gradient(135deg,#07c160 0%,#05a04e 100%);color:#fff;font-weight:600;cursor:move;user-select:none}' +
      '.nhmp-grip{font-size:12px;opacity:.7;letter-spacing:-1px}' +
      '.nhmp-title{flex:1;font-size:13px;letter-spacing:.2px}' +
      '.nhmp-toggle-btn{font-size:11px;padding:2px 8px;border-radius:12px;background:rgba(255,255,255,.22);border:1px solid rgba(255,255,255,.3);color:#fff;transition:all .15s ease}' +
      '.nhmp-toggle-btn:hover{background:rgba(255,255,255,.35)}' +
      '.nhmp-btn{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border:1px solid #d9d9d9;border-radius:6px;background:#fff;color:#333;cursor:pointer;font-size:12px;line-height:1.4;transition:all .15s ease}' +
      '.nhmp-btn:hover{border-color:#07c160;color:#07c160}' +
      '.nhmp-btn-primary{background:#07c160;border-color:#07c160;color:#fff}' +
      '.nhmp-btn-primary:hover{background:#06ad56;border-color:#06ad56;color:#fff}' +
      '.nhmp-toolbar{padding:10px 14px;border-bottom:1px solid #f0f0f0;background:#fafafa}' +
      '.nhmp-status{padding:6px 14px;font-size:12px;color:#576b95;background:#f4f6f9}' +
      '.nhmp-status.ok{color:#07c160;background:#eefbf3}.nhmp-status.err{color:#fa5151;background:#fef2f2}.nhmp-status.warn{color:#fa9d3b;background:#fffbe6}' +
      '.nhmp-list{overflow-y:auto;padding:10px 14px;display:flex;flex-direction:column;gap:10px}' +
      '.nhmp-empty{color:#999;padding:24px 0;text-align:center}' +
      '.nhmp-row{border:1px solid #e8e8e8;border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:8px;background:#fff;transition:border-color .15s ease,box-shadow .15s ease}' +
      '.nhmp-row:hover{border-color:#07c160;box-shadow:0 2px 8px rgba(7,193,96,.12)}' +
      '.nhmp-row-title{font-weight:600;word-break:break-all;color:#1f1f1f}' +
      '.nhmp-row-meta{color:#8c8c8c;font-size:11px}' +
      '.nhmp-row-actions{display:flex;gap:8px}' +
      '.nhmp-footer{padding:8px 14px;border-top:1px solid #f0f0f0;color:#8c8c8c;font-size:11px;background:#fafafa}'
    )

    GM_registerMenuCommand('设置 Notify-Hub 地址', function () {
      var next = prompt('Notify-Hub 后台地址（含端口，如 http://localhost:8000）', base)
      if (next === null) return
      base = fmt(next)
      setConfig(STORE_BASE, base)
      toast('地址已保存：' + base, 'ok')
    })

    GM_registerMenuCommand('设置访问令牌', function () {
      var next = prompt('粘贴 Notify-Hub 管理员访问令牌（后台登录后从浏览器存储中复制，或调用登录接口获取）', token)
      if (next === null) return
      token = String(next).trim()
      setConfig(STORE_TOKEN, token)
      toast('令牌已保存', 'ok')
    })

    GM_registerMenuCommand('刷新文章列表', loadArticles)

    GM_registerMenuCommand('重置面板位置', function () {
      GM_setValue(POS_KEY, '')
      if (panel) {
        panel.style.top = '25%'
        panel.style.right = '16px'
        panel.style.left = 'auto'
      }
      toast('面板位置已重置为默认（右侧贴边）', 'ok')
    })

    if (!onEditorPage()) return
    buildPanel()
    loadArticles()
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot)
  } else {
    boot()
  }
})()