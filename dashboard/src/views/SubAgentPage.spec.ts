import { describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import SubAgentPage from './SubAgentPage.vue'

const { getConfig, updateConfig, listMcp } = vi.hoisted(() => ({ getConfig: vi.fn(), updateConfig: vi.fn(), listMcp: vi.fn() }))

vi.mock('@/api/v1', () => ({
  subagentApi: { getConfig, updateConfig, availableTools: vi.fn().mockResolvedValue({ data: { status: 'ok', data: [] } }) },
  mcpApi: { list: listMcp }
}))
vi.mock('vuetify', () => ({ useTheme: () => ({ global: { current: { value: { dark: false } } } }) }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))
vi.mock('@/i18n/composables', () => ({ useModuleI18n: () => ({ tm: (key: string) => key }) }))
vi.mock('@/utils/confirmDialog', () => ({ useConfirmDialog: () => ({}), askForConfirmation: vi.fn() }))
vi.mock('@/components/shared/PersonaQuickPreview.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/components/shared/PersonaSelector.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/components/shared/ProviderSelector.vue', () => ({ default: { template: '<div />' } }))

describe('SubAgentPage', () => {
  it('hides DAG settings and omits legacy DAG fields when saving', async () => {
    listMcp.mockResolvedValue({ data: { status: 'ok', data: [] } })
    getConfig.mockResolvedValue({ data: { status: 'ok', data: { dag_enabled: true, dag_max_nodes: 42 } } })
    updateConfig.mockResolvedValue({ data: { status: 'ok' } })
    const stubs = Object.fromEntries([
      'VChip', 'VBtn', 'VSwitch', 'VSelect', 'VTextField', 'VDivider', 'VTextarea',
      'VExpandTransition', 'VBadge', 'VCardTitle', 'VCombobox', 'VListItemTitle',
      'VListItemSubtitle', 'VListItem', 'VList', 'VCardText', 'VSpacer',
      'VCardActions', 'VCard', 'VDialog', 'VSnackbar', 'VAlert'
    ].map(name => [name, true]))
    const wrapper = shallowMount(SubAgentPage, { global: { stubs: { ...stubs, VContainer: { template: '<div><slot /></div>' } } } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('dag.orchestrationMode')
    await (wrapper.vm as any).save()
    expect(updateConfig).toHaveBeenCalledOnce()
    const payload = updateConfig.mock.calls[0][0]
    expect(payload).not.toHaveProperty('dag_enabled')
    expect(payload).not.toHaveProperty('dag_max_nodes')
    expect(payload).not.toHaveProperty('dag_max_parallel')
    expect(payload).not.toHaveProperty('dag_max_inject_length')
    wrapper.unmount()
  })

  it('saves the current default blacklist after restoring it', async () => {
    getConfig.mockResolvedValue({ data: { status: 'ok', data: {
      dynamic_agents: { tools_blacklist: ['orchestrate_tasks', 'custom_tool'] }
    } } })
    updateConfig.mockClear().mockResolvedValue({ data: { status: 'ok' } })
    const wrapper = shallowMount(SubAgentPage, {
      global: {
        stubs: { VContainer: { template: '<div><slot /></div>' } },
        config: { warnHandler: () => {} }
      }
    })
    await flushPromises()
    ;(wrapper.vm as any).resetBlacklistToDefault()
    await (wrapper.vm as any).save()
    expect(updateConfig.mock.calls[0][0].dynamic_agents.tools_blacklist).toEqual([
      'create_subagent',
      'remove_subagent',
      'list_subagents',
      'wait_for_subagent',
      'broadcast_shared_context',
      'view_shared_context'
    ])
    wrapper.unmount()
  })

  it('offers active runtime MCP servers in the subagent policy', async () => {
    getConfig.mockResolvedValue({ data: { status: 'ok', data: { dynamic_agents: { mcp_inherent: ['offline'] } } } })
    listMcp.mockResolvedValue({ data: { status: 'ok', data: [
      { name: 'plugin-server', policy_name: 'runtime:plugin-server', source: 'runtime', readonly: true, active: true },
      { name: 'disabled', source: 'config', readonly: false, active: false }
    ] } })
    const wrapper = shallowMount(SubAgentPage, {
      global: { config: { warnHandler: () => {} } }
    })
    await flushPromises()
    expect((wrapper.vm as any).availableMcpServers).toEqual([
      { title: 'plugin-server (enhancedTools.runtimeMcp)', value: 'runtime:plugin-server', conflict: false },
      { title: 'offline', value: 'offline', conflict: false }
    ])
    ;(wrapper.vm as any).dynamicCfg.mcp_blacklist = ['runtime:plugin-server']
    updateConfig.mockClear().mockResolvedValue({ data: { status: 'ok' } })
    await (wrapper.vm as any).save()
    expect(updateConfig.mock.calls[0][0].dynamic_agents).toMatchObject({
      mcp_blacklist: ['runtime:plugin-server'], mcp_inherent: ['offline']
    })
    wrapper.unmount()
  })

  it('warns about policy collisions and disables both ambiguous inherent choices', async () => {
    getConfig.mockResolvedValue({ data: { status: 'ok', data: { dynamic_agents: { mcp_inherent: ['runtime:plugin'] } } } })
    listMcp.mockResolvedValue({ data: { status: 'ok', data: [
      { name: 'plugin', policy_name: 'runtime:plugin', source: 'runtime', active: true, policy_conflict: true },
      { name: 'runtime:plugin', policy_name: 'runtime:plugin', source: 'config', active: true, policy_conflict: true }
    ] } })
    const wrapper = shallowMount(SubAgentPage, { global: {
      config: { warnHandler: () => {} },
      stubs: { VContainer: { template: '<div><slot /></div>' }, VAlert: { template: '<div><slot /></div>' } }
    } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.conflictingMcpPolicies).toEqual(['runtime:plugin'])
    expect(wrapper.text()).toContain('enhancedTools.mcpPolicyConflict')
    expect(vm.inherentMcpServers.every((option: any) => option.props.disabled)).toBe(true)
    expect(vm.availableMcpServers.every((option: any) => !option.props?.disabled)).toBe(true)
    expect(vm.dynamicCfg.mcp_inherent).toEqual(['runtime:plugin'])
    wrapper.unmount()
  })
})
