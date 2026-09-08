import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setApiFetcher } from '@/lib/api'
import ArticlesView from '@/views/ArticlesView.vue'
import type { MpArticle, Page } from '@/types'

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

  it('renders the standalone Browser Publisher mode banner', async () => {
    setApiFetcher(fetcher([], { effective_mode: 'browser' }))
    const wrapper = mount(ArticlesView, { global: { plugins: [createPinia()] } })
    await flushPromises()

    expect(wrapper.text()).toContain('浏览器自动发布模式')
    expect(wrapper.text()).toContain('独立 Browser Publisher')
    wrapper.unmount()
  })
})
