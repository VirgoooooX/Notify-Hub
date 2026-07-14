import{mount}from'@vue/test-utils'
import{describe,expect,it}from'vitest'
import StatusBadge from'@/components/StatusBadge.vue'
describe('StatusBadge',()=>{it('renders a localized known status',()=>{const wrapper=mount(StatusBadge,{props:{status:'retry_wait'}});expect(wrapper.text()).toContain('等待重试');expect(wrapper.classes()).toContain('status--retry_wait')});it('keeps unknown backend states visible',()=>{expect(mount(StatusBadge,{props:{status:'future_state'}}).text()).toContain('future_state')})})
