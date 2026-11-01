import { beforeEach, describe, expect, it } from 'vitest';
import { buildWebchatUmoDetails } from './chatConfigBinding';

describe('WebChat resource identity', () => {
  beforeEach(() => localStorage.clear());

  it('keeps existing UMO bindings when the display login changes', () => {
    localStorage.setItem('user', 'renamed-admin');
    localStorage.setItem('resource_owner', 'original-admin');
    expect(buildWebchatUmoDetails('existing-session').umo)
      .toBe('webchat:FriendMessage:webchat!original-admin!existing-session');
  });

  it('retains compatibility with clients signed in before the upgrade', () => {
    localStorage.setItem('user', 'original-admin');
    expect(buildWebchatUmoDetails('existing-session').username).toBe('original-admin');
  });
});
