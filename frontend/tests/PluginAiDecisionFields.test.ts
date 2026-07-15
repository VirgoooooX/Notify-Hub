import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PluginAiDecisionFields from '@/features/plugins/PluginAiDecisionFields.vue'

describe('PluginAiDecisionFields', () => {
  it('edits the rule confidence threshold only in rules_then_ai mode', async () => {
    const wrapper = mount(PluginAiDecisionFields, {
      props: {
        decisionMode: 'rules_then_ai',
        aiProfile: 'semantic_classifier_fast',
        aiMinConfidence: 0.8,
        ruleAiThreshold: 0.8,
        allowedProfiles: [],
      },
    })

    expect(wrapper.text()).toContain('规则置信度低于该值时才调用 AI')
    const inputs = wrapper.findAll<HTMLInputElement>('input[type="number"]')
    expect(inputs).toHaveLength(2)
    expect(inputs[1].element.disabled).toBe(false)
    await inputs[1].setValue('0.9')
    expect(wrapper.emitted('update:ruleAiThreshold')?.at(-1)).toEqual([0.9])

    await wrapper.setProps({ decisionMode: 'rules' })
    expect(inputs[1].element.disabled).toBe(true)
  })
})
