import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import { flushPromises, shallowMount } from "@vue/test-utils";
import Settings from "./Settings.vue";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  access: vi.fn(),
  setEnabled: vi.fn(),
}));
vi.mock("@/api/v1", () => ({
  systemConfigApi: { get: api.get, update: api.update },
  apiKeyApi: {
    list: vi.fn(async () => ({ data: { status: "ok", data: [] } })),
  },
  updatesApi: {
    sources: vi.fn(async () => ({ data: { status: "ok", data: {} } })),
  },
  remoteChatApi: { access: api.access, setEnabled: api.setEnabled },
}));
vi.mock("@/i18n/composables", () => ({
  useModuleI18n: () => ({ tm: (key: string) => key }),
  useI18n: () => ({ t: (key: string) => key, locale: ref("en-US") }),
}));
vi.mock("vuetify", () => ({ useTheme: () => ({}) }));
vi.mock("@/stores/toast", () => ({ useToastStore: () => ({ add: vi.fn() }) }));
vi.mock("@/utils/confirmDialog", () => ({
  askForConfirmation: vi.fn(),
  useConfirmDialog: () => ({}),
}));

const switchStub = defineComponent({
  props: ["modelValue", "disabled", "label"],
  emits: ["update:modelValue"],
  setup:
    (props, { emit }) =>
    () =>
      h("input", {
        type: "checkbox",
        "aria-label": props.label,
        checked: props.modelValue,
        disabled: props.disabled,
        onChange: (event: any) =>
          emit("update:modelValue", event.target.checked),
      }),
});
const configStub = defineComponent({
  props: ["iterable", "metadataKey"],
  setup: (props) => () =>
    h(
      "div",
      (props.metadataKey === "runtime"
        ? ["timezone"]
        : props.metadataKey === "logs"
        ? ["log_level"]
        : []
      ).map((key) =>
        h("input", {
          "data-setting": key,
          value: props.iterable[key],
          onInput: (event: any) => {
            props.iterable[key] = event.target.value;
          },
        }),
      ),
    ),
});
const stubs = {
  RemoteChatAccessPanel: false,
  AstrBotConfigV4: configStub,
  "v-switch": switchStub,
  "v-btn": true,
  "v-slide-y-reverse-transition": true,
  "v-progress-linear": true,
  "v-text-field": true,
  "v-select": true,
  "v-alert": true,
  "v-checkbox": true,
  "v-chip": true,
  "v-table": true,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Settings remote connections use the full configuration save chain", () => {
  it.each([true, false])(
    "preserves the toggle after other settings save (initial=%s)",
    async (initial) => {
      let persisted = {
        chatui: {
          remote_sessions_enabled: initial,
          remote_session_creation_enabled: initial,
        },
        timezone: "Asia/Hong_Kong",
        log_level: "INFO",
      };
      api.get.mockImplementation(async () => ({
        data: {
          status: "ok",
          data: {
            config: structuredClone(persisted),
            metadata: {
              system_group: {
                metadata: {
                  system: { items: { timezone: {}, log_level: {} } },
                },
              },
            },
          },
        },
      }));
      api.update.mockImplementation(async (config) => {
        persisted = structuredClone(config);
        return { status: 200, data: { status: "ok" } };
      });
      api.access.mockImplementation(async () => ({
        data: {
          data: {
            enabled: persisted.chatui.remote_sessions_enabled,
            mode: "selected",
            session_ids: [],
            sessions: [],
            credentials: [],
          },
        },
      }));
      api.setEnabled.mockImplementation(async (enabled) => {
        persisted.chatui.remote_sessions_enabled = enabled;
      });
      let wrapper = shallowMount(Settings, { global: { stubs } });
      try {
        await flushPromises();
        await wrapper
          .findAll("nav button")
          .find((button) => button.text() === "sections.security.title")!
          .trigger("click");
        await wrapper
          .find('input[aria-label="remoteAccess.enabled"]')
          .setValue(!initial);
        await wrapper
          .find('input[aria-label="remoteAccess.allowCreation"]')
          .setValue(!initial);
        await vi.advanceTimersByTimeAsync(800);
        await flushPromises();
        expect(persisted.chatui.remote_session_creation_enabled).toBe(!initial);
        expect(persisted.chatui.remote_sessions_enabled).toBe(!initial);
        expect(api.setEnabled).not.toHaveBeenCalled();
        await wrapper
          .findAll("nav button")
          .find((button) => button.text() === "sections.general.title")!
          .trigger("click");
        await wrapper.find('input[data-setting="timezone"]').setValue("UTC");
        await vi.advanceTimersByTimeAsync(800);
        await flushPromises();
        expect(persisted.timezone).toBe("UTC");
        expect(persisted.chatui.remote_session_creation_enabled).toBe(!initial);
        expect(persisted.chatui.remote_sessions_enabled).toBe(!initial);
        await wrapper.find('input[data-setting="log_level"]').setValue("DEBUG");
        await vi.advanceTimersByTimeAsync(800);
        await flushPromises();
        expect(persisted.log_level).toBe("DEBUG");
        expect(persisted.chatui.remote_sessions_enabled).toBe(!initial);
        expect(
          api.update.mock.calls.every(
            ([config]) => config.chatui.remote_sessions_enabled === !initial,
          ),
        ).toBe(true);
        wrapper.unmount();
        wrapper = shallowMount(Settings, { global: { stubs } });
        await flushPromises();
        expect(
          (
            wrapper.find('input[aria-label="remoteAccess.enabled"]')
              .element as HTMLInputElement
          ).checked,
        ).toBe(!initial);
      } finally {
        wrapper.unmount();
      }
    },
  );
});
