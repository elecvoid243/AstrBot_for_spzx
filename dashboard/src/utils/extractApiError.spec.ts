// Specs for the pure API-error extractor: resolves the backend's human-readable
// reason from an axios rejection (the HTTP 400/409 error envelope body), the
// 429 plain-string rejection, a generic Error, or falls back — and passes the
// structured validation `fields` array through for Plan-2 consumption.
import { describe, expect, it } from 'vitest';
import { extractApiError } from './extractApiError';

/** Build an axios-shaped rejection: non-2xx with the envelope body attached. */
function axiosError(data: unknown, status = 400) {
  return {
    message: `Request failed with status code ${status}`,
    response: { status, data },
  };
}

describe('extractApiError', () => {
  it('prefers the backend envelope message on an axios HTTP error', () => {
    const err = axiosError({ status: 'error', message: '成员名必须 unique: Alice' });
    expect(extractApiError(err, '保存失败')).toEqual({
      message: '成员名必须 unique: Alice',
      fields: [],
    });
  });

  it('passes structured validation fields through alongside the message', () => {
    const fields = [
      { path: 'members.1.name', message: 'duplicate name' },
      { path: 'name', message: 'required' },
    ];
    const err = axiosError({
      status: 'error',
      message: 'validation failed',
      data: { fields },
    });
    expect(extractApiError(err, '保存失败')).toEqual({ message: 'validation failed', fields });
  });

  it('resolves a plain-string rejection (the 429 rate-limit case)', () => {
    // normalizeAxiosError rejects 429s with data.message as a bare string.
    expect(extractApiError('请求过于频繁，请稍后再试', '保存失败')).toEqual({
      message: '请求过于频繁，请稍后再试',
      fields: [],
    });
  });

  it('falls back to Error.message for non-axios errors', () => {
    expect(extractApiError(new Error('network down'), '保存失败')).toEqual({
      message: 'network down',
      fields: [],
    });
  });

  it('uses the fallback for unknown rejection shapes', () => {
    const fallback = { message: '操作失败', fields: [] };
    expect(extractApiError(undefined, '操作失败')).toEqual(fallback);
    expect(extractApiError(null, '操作失败')).toEqual(fallback);
    expect(extractApiError({}, '操作失败')).toEqual(fallback);
    expect(extractApiError(42, '操作失败')).toEqual(fallback);
    expect(extractApiError({ response: {} }, '操作失败')).toEqual(fallback);
  });

  it('falls back when the envelope message is an empty string', () => {
    const err = axiosError({ status: 'error', message: '' });
    expect(extractApiError(err, '保存失败').message).toBe('保存失败');
  });

  it('keeps the message but drops malformed fields payloads', () => {
    const notArray = axiosError({
      status: 'error',
      message: 'bad fields',
      data: { fields: 'nope' },
    });
    expect(extractApiError(notArray, 'f')).toEqual({ message: 'bad fields', fields: [] });

    const badEntries = axiosError({
      status: 'error',
      message: 'bad fields',
      data: { fields: [{ path: 'a' }, 'x', null, { path: 'b', message: 'ok' }] },
    });
    expect(extractApiError(badEntries, 'f').fields).toEqual([{ path: 'b', message: 'ok' }]);
  });

  it('never returns an empty message', () => {
    // A whitespace-only envelope message is not user-visible either.
    const err = axiosError({ status: 'error', message: '   ' });
    expect(extractApiError(err, '保存失败').message).toBe('保存失败');
    expect(extractApiError('   ', '保存失败').message).toBe('保存失败');
  });
});
