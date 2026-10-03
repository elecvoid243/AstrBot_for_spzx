// Author: elecvoid243, 2026-07-09
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import GitDiffChip from './GitDiffChip.vue'

describe('GitDiffChip (status-row workspace capsule)', () => {
  it('renders the sp-ghost-btn class (no chip border)', () => {
    const wrapper = mount(GitDiffChip, {
      global: { mocks: { $t: (k: string) => k } },
    })
    expect(wrapper.find('.sp-ghost-btn').exists()).toBe(true)
    // The old v-chip class should NOT be present
    expect(wrapper.find('.git-diff-chip').exists()).toBe(false)
  })

  it('uses mdi-folder-open-outline (lighter icon than the old mdi-folder-open)', () => {
    const wrapper = mount(GitDiffChip, {
      global: { mocks: { $t: (k: string) => k } },
    })
    expect(wrapper.find('.mdi-folder-open-outline').exists()).toBe(true)
  })

  it('emits toggle-diff-sidebar on click', async () => {
    const wrapper = mount(GitDiffChip, {
      global: { mocks: { $t: (k: string) => k } },
    })
    await wrapper.find('.sp-ghost-btn').trigger('click')
    expect(wrapper.emitted('toggle-diff-sidebar')).toBeTruthy()
  })

  // The label span is asserted directly: the tooltip carries the shorter
  // text too, so a wrapper.text() match could pass on the wrong node.
  it('renders the short 工作区 label from i18n', () => {
    const wrapper = mount(GitDiffChip, {
      global: { mocks: { $t: (k: string) => k } },
    })
    expect(wrapper.find('.sp-ghost-btn__label').text()).toBe('工作区')
  })

  it('tints the capsule while the sidebar is open', () => {
    const wrapper = mount(GitDiffChip, {
      props: { active: true },
      global: { mocks: { $t: (k: string) => k } },
    })
    const btn = wrapper.find('.sp-ghost-btn')
    expect(btn.classes()).toContain('sp-ghost-btn--active')
    expect(btn.attributes('aria-pressed')).toBe('true')
  })

  it('stays neutral when the sidebar is closed', () => {
    const wrapper = mount(GitDiffChip, {
      global: { mocks: { $t: (k: string) => k } },
    })
    const btn = wrapper.find('.sp-ghost-btn')
    expect(btn.classes()).not.toContain('sp-ghost-btn--active')
    expect(btn.attributes('aria-pressed')).toBe('false')
  })
})
