import { describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import McpServersSection from './McpServersSection.vue'

const { list, setEnabled, remove, update } = vi.hoisted(() => ({
  list: vi.fn(), setEnabled: vi.fn(), remove: vi.fn(), update: vi.fn()
}))
vi.mock('@/api/v1', () => ({ mcpApi: { list, setEnabled, delete: remove, update } }))
vi.mock('@/i18n/composables', () => ({
  useI18n: () => ({ t: (key: string) => key }),
  useModuleI18n: () => ({ tm: (key: string) => key })
}))
vi.mock('@/utils/confirmDialog', () => ({ useConfirmDialog: () => ({}), askForConfirmation: vi.fn() }))
vi.mock('@/components/shared/LazyMonacoEditor', () => ({ LazyMonacoEditor: {} }))

describe('McpServersSection', () => {
  it('shows runtime servers but excludes them from editing and batch selection', async () => {
    const plugin = { name: 'plugin-server', source: 'runtime', readonly: true, active: true, connected: true, tools: ['tool'] }
    const configured = { name: 'configured', source: 'config', readonly: false, active: true, connected: true, tools: [] }
    list.mockResolvedValue({ data: { status: 'ok', data: [plugin, configured] } })
    const wrapper = shallowMount(McpServersSection, { global: { config: { warnHandler: () => {} } } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.filteredMcpServers.map((server: any) => server.name)).toEqual(['plugin-server', 'configured'])
    expect(vm.getServerConfigSummary(plugin)).toBe('mcpServers.pluginManaged')
    vm.editServer(plugin)
    vm.updateServerStatus(plugin)
    expect(vm.showMcpServerDialog).toBe(false)
    expect(setEnabled).not.toHaveBeenCalled()
    vm.startBatchSelection()
    vm.toggleSelectAll()
    expect(vm.selectedMcpServerNames).toEqual(['configured'])
    vm.confirmBatchDelete()
    expect(vm.batchDeleteTargets).toEqual(['configured'])
    wrapper.unmount()
  })

  it('does not save response metadata when editing a configured server', async () => {
    const server = { name: 'configured', policy_name: 'configured', policy_conflict: false, source: 'config', readonly: false, active: true, connected: true, tools: [], command: 'uv' }
    list.mockResolvedValue({ data: { status: 'ok', data: [server] } })
    update.mockResolvedValue({ data: { status: 'ok' } })
    const wrapper = shallowMount(McpServersSection, { global: { config: { warnHandler: () => {} } } })
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.getServerConfigSummary({ name: 'empty', policy_name: 'empty', policy_conflict: false, source: 'config', readonly: false })).toBe('mcpServers.status.noConfig')
    vm.editServer(server)
    vm.saveServer()
    await flushPromises()
    expect(update).toHaveBeenCalledWith('configured', { name: 'configured', active: true, command: 'uv', oldName: 'configured' })
    wrapper.unmount()
  })

  it('uses distinct keys for servers sharing a conflicting policy identifier', async () => {
    list.mockResolvedValue({ data: { status: 'ok', data: [
      { name: 'plugin', policy_name: 'runtime:plugin', source: 'runtime', readonly: true, policy_conflict: true, active: true, connected: true, tools: [] },
      { name: 'runtime:plugin', policy_name: 'runtime:plugin', source: 'config', readonly: false, policy_conflict: true, active: true, connected: true, tools: [] }
    ] } })
    const wrapper = shallowMount(McpServersSection, { global: {
      config: { warnHandler: () => {} },
      stubs: { VContainer: { template: '<div><slot /></div>' } }
    } })
    await flushPromises()
    const items = wrapper.findAllComponents({ name: 'OutlinedActionListItem' })
    expect(items).toHaveLength(2)
    expect(items.map(item => item.vm.$.vnode.key)).toEqual(['runtime:runtime:plugin', 'config:runtime:plugin'])
    wrapper.unmount()
  })
})
