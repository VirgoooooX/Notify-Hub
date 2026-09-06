import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import ArticlesView from '@/views/ArticlesView.vue'
import type { MpArticle, MpBrowserSession, Page } from '@/types'

const article: MpArticle = {
  id: 'mpa_1',
  status: 'ready',
  title: 'Codex 用量可能已重置',
  author: 'Notify Hub',
  digest: '摘要内容',
  content: '第一段\n第二段',
  content_html: '<section style="font-size:16px;"><p>第一段</p><p>第二段</p></section>',
  cover_url: 'https://img.example.com/cover.png',
  source_url: 'https://x.com/post/1',
  event_key: 'x-post-1',
  source_type: 'plugin',
  source_id: 'codex_x_monitor',
  event_type: 'codex.usage_reset',
  notification_id: 'ntf_1',
  delivery_id: 'dlv_1',
  ai_profile: 'article_writer',
  ai_status: 'ai_summarized',
  provider_draft_media_id: null,
  provider_publish_id: null,
  published_at: null,
  created_at: '2026-08-13T00:00:00Z',
  updated_at: '2026-08-13T00:00:00Z',
  payload: {},
  browser_phase: null,
  browser_attempt_count: 0,
  browser_last_error_code: null,
  browser_last_error_message: null,
  published_url: null,
}

function page(items: MpArticle[]): Page<MpArticle> {
  return { items, page: 1, page_size: 20, total: items.length }
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function fetcher(
  requests: Array<{ path: string; method: string; body?: Record<string, unknown> }>,
  options?: {
    effective_mode?: 'library' | 'browser'
    session?: Partial<MpBrowserSession>
    articles?: MpArticle[]
  },
) {
  const effectiveMode = options?.effective_mode ?? 'library'
  const articlesList = options?.articles ?? [article]

  return vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = new URL(String(input), 'http://test').pathname
    const method = init?.method ?? 'GET'
    requests.push({ path, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined })
    if (path.endsWith('/admin/settings')) return json({ timezone: 'Asia/Shanghai' })
    if (path.endsWith('/admin/articles/config')) {
      return json({
        configured: false,
        publish_mode: effectiveMode,
        effective_mode: effectiveMode,
        author: 'Notify Hub',
        mp_editor_url: 'https://mp.weixin.qq.com',
      })
    }
    if (path.endsWith('/admin/mp-browser/session')) {
      return json({
        state: 'ready',
        last_seen_at: '2026-09-06T10:00:00Z',
        current_article_id: null,
        incident_id: null,
        last_error_code: null,
        last_error_message: null,
        qr_data_url: null,
        ...(options?.session || {}),
      })
    }
    if (path.endsWith('/admin/articles/mpa_1/publish')) {
      return json({ ...article, status: 'published', published_at: '2026-08-13T01:00:00Z' })
    }
    if (path.endsWith('/admin/articles/mpa_1/ignore')) {
      return json({ ...article, status: 'ignored' })
    }
    if (path.endsWith('/admin/articles/mpa_1/restore')) {
      return json({ ...article, status: 'ready' })
    }
    const articleMatch = articlesList.find((a) => path.endsWith(`/admin/articles/${a.id}`))
    if (articleMatch) return json(articleMatch)
    if (path.endsWith('/admin/articles')) return json(page(articlesList))
    throw new Error(`unexpected request: ${method} ${path}`)
  }) as typeof fetch
}

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ArticlesView', () => {
  it('lists ready articles and shows the library mode banner', async () => {
    setApiFetcher(fetcher([]))
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Codex 用量可能已重置')
    expect(wrapper.text()).toContain('AI 摘要')
    expect(wrapper.text()).toContain('文章库模式')
    wrapper.unmount()
  })

  it('renders browser mode banner and session ready indicator', async () => {
    setApiFetcher(fetcher([], { effective_mode: 'browser', session: { state: 'ready' } }))
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('浏览器自动发布模式')
    expect(wrapper.text()).toContain('Publisher 在线就绪')
    wrapper.unmount()
  })

  it('displays QR code image when session requires auth', async () => {
    setApiFetcher(
      fetcher([], {
        effective_mode: 'browser',
        session: {
          state: 'auth_required',
          qr_data_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
        },
      }),
    )
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('微信公众号登录会话已过期')
    const img = wrapper.find('img.qr-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toContain('data:image/png;base64')
    wrapper.unmount()
  })

  it('shows offline warning when publisher is offline', async () => {
    setApiFetcher(
      fetcher([], {
        effective_mode: 'browser',
        session: { state: 'offline' },
      }),
    )
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Publisher 处于离线状态 (offline)')
    wrapper.unmount()
  })

  it('renders publishing and failed status with published URL link', async () => {
    const publishedArticle: MpArticle = {
      ...article,
      id: 'mpa_pub',
      status: 'published',
      published_url: 'https://mp.weixin.qq.com/s/sample_url',
    }
    const failedArticle: MpArticle = {
      ...article,
      id: 'mpa_fail',
      status: 'failed',
      browser_last_error_code: 'EDITOR_TIMEOUT',
      browser_attempt_count: 3,
    }
    setApiFetcher(fetcher([], { articles: [publishedArticle, failedArticle] }))
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('EDITOR_TIMEOUT')
    const link = wrapper.find('a.published-link')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('https://mp.weixin.qq.com/s/sample_url')
    wrapper.unmount()
  })

  it('handles PUBLISH_RESULT_UNKNOWN in modal and suppresses blind retry', async () => {
    const unknownArticle: MpArticle = {
      ...article,
      id: 'mpa_unknown',
      status: 'failed',
      browser_last_error_code: 'PUBLISH_RESULT_UNKNOWN',
      browser_last_error_message: 'Result unknown',
      browser_phase: 'publish_clicked',
    }
    setApiFetcher(
      fetcher([], {
        effective_mode: 'browser',
        articles: [unknownArticle],
      }),
    )
    const wrapper = mount(ArticlesView, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    const viewBtn = wrapper.findAll('button').filter((btn) => btn.text().trim() === '查看')[0]
    await viewBtn.trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('实际结果未知')
    expect(document.body.textContent).toContain('确认已发布')
    expect(document.body.textContent).not.toContain('重新排队')
    wrapper.unmount()
  })
})