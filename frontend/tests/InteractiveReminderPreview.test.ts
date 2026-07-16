import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import InteractiveReminderPreview from '@/components/reminders/InteractiveReminderPreview.vue'

describe('InteractiveReminderPreview', () => {
  it('shows the exact menu hint and five operations for interactive reminders', () => {
    const wrapper = mount(InteractiveReminderPreview, {
      props: {
        title: '提交月度报表',
        content: '请在今天下班前提交',
        contentType: 'article',
        interactive: true,
      },
    })

    expect(wrapper.text()).toContain('普通图文')
    expect(wrapper.text()).toContain('🔁【持续提醒｜需要你确认完成】')
    expect(wrapper.text()).toContain('这不是一次性通知')
    expect(wrapper.text()).toContain('【快捷操作】→【完成本次】')
    expect(wrapper.text()).toContain('菜单默认操作最近收到的一条交互式提醒。')
    for (const series of ['新建提醒', '我的提醒', '快捷操作']) {
      expect(wrapper.text()).toContain(series)
    }
    for (const action of ['完成本次', '推迟10分钟', '推迟30分钟', '今日忽略', '停止本次']) {
      expect(wrapper.text()).toContain(action)
    }
  })

  it('makes an interactive all-member broadcast unmistakable', () => {
    const wrapper = mount(InteractiveReminderPreview, {
      props: {
        title: '全员提交安全确认',
        content: '请完成安全检查',
        interactive: true,
        broadcast: true,
        notifyOnAllCompleted: true,
      },
    })

    expect(wrapper.text()).toContain('📣 全员持续提醒｜全员提交安全确认')
    expect(wrapper.text()).toContain('📣【全员持续提醒｜需要每个人确认】')
    expect(wrapper.text()).toContain('所有登记接收人完成后，系统会广播“所有人都完成”。')
  })

  it('does not promise an all-completed broadcast when the option is off', () => {
    const wrapper = mount(InteractiveReminderPreview, {
      props: {
        title: '全员普通确认',
        interactive: true,
        broadcast: true,
      },
    })

    expect(wrapper.text()).toContain('📣【全员持续提醒｜需要每个人确认】')
    expect(wrapper.text()).not.toContain('所有登记接收人完成后')
  })

  it('explains that a normal notification does not replace the interactive target', () => {
    const wrapper = mount(InteractiveReminderPreview, {
      props: { title: '系统通知', interactive: false },
    })

    expect(wrapper.text()).toContain('普通通知不会更新用户的“最近交互提醒”指针。')
    expect(wrapper.text()).not.toContain('推迟10分钟')
  })
})
