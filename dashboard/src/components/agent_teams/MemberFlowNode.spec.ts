// Specs for the MemberFlowNode card (Task 3): header (title / #number / status
// dot + status TEXT — not color-only), body (member color dot + name, missing
// label, config/persona chips, 2-line task preview), footer (入站依赖) and the
// status/error/missing/selected class matrix.
//
// The card is mounted directly with `data.interactive === false` — the
// sanctioned test seam that hides the Vue Flow handles so no node context
// provider is needed (production always passes true/undefined through the
// canvas' nodeTypes registration).
import { mount } from '@vue/test-utils';
import type { VueWrapper } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import zh from '@/i18n/locales/zh-CN/features/agent-teams.json';
import MemberFlowNode from './MemberFlowNode.vue';

const BASE_DATA = {
  memberName: 'Alice',
  memberColor: '#e53935',
  nodeTitle: 'n1',
  nodeNumber: 1,
  taskPreview: 'Fix the login bug',
  status: 'running',
  inDegree: 2,
  interactive: false,
};

function mountNode(data: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
  return mount(MemberFlowNode, {
    props: { id: 'n1', data: { ...BASE_DATA, ...data }, ...props },
  }) as VueWrapper<any>;
}

function card(wrapper: VueWrapper<any>) {
  return wrapper.find('.member-flow-node');
}

describe('MemberFlowNode header', () => {
  it('renders the node title, #number and status dot plus status text', () => {
    const wrapper = mountNode();

    expect(wrapper.find('.at-node-title').text()).toBe('n1');
    expect(wrapper.find('.at-node-number').text()).toBe('#1');
    // Status is communicated as text (reuse monitor.node.* keys), not only by
    // the dot color.
    const status = wrapper.find('.at-node-status');
    expect(status.exists()).toBe(true);
    expect(status.text()).toBe(zh.monitor.node.running);
    expect(status.attributes('data-status')).toBe('running');
    expect(wrapper.find('.at-node-status-dot').exists()).toBe(true);
    // The status ring class lands on the card root.
    expect(card(wrapper).classes()).toContain('at-node-running');
  });

  it('falls back to the node label when no nodeTitle is set and omits the number', () => {
    const wrapper = mountNode({ nodeTitle: undefined, nodeNumber: undefined, label: 'Alice (n1)' });

    expect(wrapper.find('.at-node-title').text()).toBe('Alice (n1)');
    expect(wrapper.find('.at-node-number').exists()).toBe(false);
  });

  it('renders status text for every monitor status key including interrupted', () => {
    const statusTexts = zh.monitor.node as Record<string, string>;
    for (const status of ['pending', 'running', 'done', 'failed', 'skipped', 'interrupted']) {
      const wrapper = mountNode({ status });
      expect(wrapper.find('.at-node-status').text()).toBe(statusTexts[status]);
      wrapper.unmount();
    }
  });

  it('hides the status block when the node has no status yet', () => {
    const wrapper = mountNode({ status: undefined });
    expect(wrapper.find('.at-node-status').exists()).toBe(false);
  });
});

describe('MemberFlowNode body', () => {
  it('renders the member color dot with the member name', () => {
    const wrapper = mountNode();

    const dot = wrapper.find('.at-node-member-dot');
    expect(dot.exists()).toBe(true);
    expect(dot.attributes('style')).toContain('#e53935');
    expect(wrapper.find('.at-node-member-name').text()).toBe('Alice');
    expect(wrapper.text()).not.toContain(zh.editor.memberMissing);
  });

  it('shows the 缺失成员 label instead of the member name and flags the card', () => {
    const wrapper = mountNode({ missingMember: true });

    expect(card(wrapper).classes()).toContain('is-missing');
    expect(wrapper.find('.at-node-member-missing').text()).toBe(zh.editor.memberMissing);
    expect(wrapper.find('.at-node-member-name').exists()).toBe(false);
  });

  it('renders config/persona chips only when the labels are present', () => {
    const wrapper = mountNode({ configLabel: 'Profile One', personaLabel: 'writer' });
    const chips = wrapper.findAll('.at-node-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].text()).toBe('Profile One');
    expect(chips[1].text()).toBe('writer');
    wrapper.unmount();

    const bare = mountNode({ configLabel: undefined, personaLabel: undefined });
    expect(bare.findAll('.at-node-chip')).toHaveLength(0);
  });

  it('renders the task preview with the 2-line clamp class', () => {
    const wrapper = mountNode();
    expect(wrapper.find('.at-node-task').text()).toBe('Fix the login bug');
    const taskClass = wrapper.find('.at-node-task').classes().join(' ');
    expect(taskClass).toContain('at-node-task-clamp');
    wrapper.unmount();

    const noTask = mountNode({ taskPreview: undefined });
    expect(noTask.find('.at-node-task').exists()).toBe(false);
  });
});

describe('MemberFlowNode footer', () => {
  it('renders the inbound dependency count', () => {
    const wrapper = mountNode();
    expect(wrapper.find('.at-node-indegree').text()).toBe(`${zh.editor.inDegree} 2`);
  });

  it('defaults the inbound count to 0 when inDegree is missing', () => {
    const wrapper = mountNode({ inDegree: undefined });
    expect(wrapper.find('.at-node-indegree').text()).toBe(`${zh.editor.inDegree} 0`);
  });
});

describe('MemberFlowNode states and handles', () => {
  it('marks the card with has-error and exposes the error as a native tooltip', () => {
    const wrapper = mountNode({ status: 'failed', error: '模型返回 500' });

    expect(card(wrapper).classes()).toContain('has-error');
    expect(card(wrapper).classes()).toContain('at-node-failed');
    expect(card(wrapper).attributes('title')).toBe('模型返回 500');
  });

  it('renders no error tooltip on a clean card', () => {
    const wrapper = mountNode({ error: undefined });
    expect(card(wrapper).classes()).not.toContain('has-error');
    expect(card(wrapper).attributes('title')).toBeUndefined();
  });

  it('marks the card as selected through the selected prop', () => {
    const wrapper = mountNode({}, { selected: true });
    expect(card(wrapper).classes()).toContain('selected');

    const unselected = mountNode();
    expect(card(unselected).classes()).not.toContain('selected');
  });

  it('hides the Vue Flow handles when interactive is false (test seam)', () => {
    const wrapper = mountNode();
    expect(wrapper.find('.vue-flow__handle').exists()).toBe(false);
  });

  it('does not leak Vue Flow internal props onto the card DOM', () => {
    // NodeWrapper forwards position/dimensions/zIndex/... to the card; they
    // must not become garbage DOM attributes on the root element.
    const wrapper = mount(MemberFlowNode, {
      props: {
        id: 'n1',
        data: { ...BASE_DATA },
        type: 'member',
        position: { x: 10, y: 20 },
        dimensions: { width: 220, height: 100 },
        zIndex: 3,
        dragging: false,
        connectable: true,
      } as any,
    });
    const attrs = Object.keys(card(wrapper).attributes());
    expect(attrs).not.toContain('position');
    expect(attrs).not.toContain('dimensions');
    expect(attrs).not.toContain('zindex');
    expect(attrs).not.toContain('connectable');
  });

  it('renders the missing-member marker as a labelled button (Plan 3 T8)', () => {
    // Keyboard users must be able to reach the fix affordance; the click then
    // bubbles into Vue Flow's node click, which selects the node.
    const wrapper = mountNode({ missingMember: true });
    const marker = wrapper.find('[data-test="missing-marker"]');

    expect(marker.exists()).toBe(true);
    expect(marker.element.tagName).toBe('BUTTON');
    expect(marker.attributes('aria-label')).toBeTruthy();
  });

  it('labels both connect handles for assistive tech (Plan 3 T8)', () => {
    // interactive defaults to false in this spec's seam, so mount with the
    // handles enabled and assert on the rendered stubs' labels.
    const wrapper = mount(MemberFlowNode, {
      props: { id: 'n1', data: { ...BASE_DATA, interactive: true } },
      global: {
        stubs: {
          Handle: {
            props: ['type', 'position'],
            template: '<div class="handle-stub" :data-type="type" />',
          },
        },
      },
    });

    const handles = wrapper.findAll('.handle-stub');
    expect(handles).toHaveLength(2);
    for (const handle of handles) {
      expect(handle.attributes('aria-label')).toBeTruthy();
    }
  });
});
