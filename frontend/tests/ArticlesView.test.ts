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

function fetcher(requests: Array<{ path: string; method: string; body?: Record<string, unknown> }>) {
  return vi.fn(async (input: unknown, init?: RequestInit) => {
    const path = new URL(String(input), 'http://test').pathname
    const method = init?.method ?? 'GET'
    requests.push({ path, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined })
    if (path.endsWith('/admin/settings')) return json({ timezone: 'Asia/Shanghai' })
    if (path.endsWith('/admin/articles/config')) {
      return json({
        configured: false,
        publish_mode: 'publish',
        effective_mode: 'library',
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
    if (path.endsWith('/admin/articles/mpa_1')) return json(article)
    if (path.endsWith('/admin/articles')) return json(page([article]))
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

  it('opens the article preview and copies the WeChat HTML format', async () => {
    const requests: Array<{ path: string; method: string }> = []
    setApiFetcher(fetcher(requests))
    const write = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write },
    })
    Object.defineProperty(window, 'ClipboardItem', {
      configurable: true,
      value: class ClipboardItem {
        constructor(public payload: Record<string, Blob>) {}
      },
    })

    const wrapper = mount(ArticlesView, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    const viewButtons = wrapper.findAll('button').filter((button) => button.text().trim() === '查看')
    await viewButtons[0].trigger('click')
    await flushPromises()

    expect(document.body.textContent).toContain('文章预览')
    expect(document.body.textContent).toContain('第一段')

    const copyButton = [...document.body.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('复制公众号格式'),
    ) as HTMLButtonElement
    copyButton.click()
    await flushPromises()

    expect(write).toHaveBeenCalledTimes(1)
    expect(requests).toContainEqual({ path: '/api/v1/admin/articles/mpa_1', method: 'GET' })

    wrapper.unmount()
  })

  it('marks an article as published through the admin API', async () => {
    const requests: Array<{ path: string; method: string }> = []
    setApiFetcher(fetcher(requests))
    const wrapper = mount(ArticlesView, {
      attachTo: document.body,
      global: { plugins: [createPinia()] },
    })
    await flushPromises()

    const viewButtons = wrapper.findAll('button').filter((button) => button.text().trim() === '查看')
    await viewButtons[0].trigger('click')
    await flushPromises()

    const publishButton = [...document.body.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('标记已发布'),
    ) as HTMLButtonElement
    publishButton.click()
    await flushPromises()

    expect(requests).toContainEqual({
      path: '/api/v1/admin/articles/mpa_1/publish',
      method: 'POST',
    })
    expect(document.body.textContent).toContain('已发布')

    wrapper.unmount()
  })
})