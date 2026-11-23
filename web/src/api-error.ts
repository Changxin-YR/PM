export class PmoHttpError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message)
  }
}

export function isRejectedWrite(error: unknown): error is PmoHttpError {
  return error instanceof PmoHttpError && [401, 403, 404, 409, 422].includes(error.status)
    && error.code !== 'REQUEST_IN_PROGRESS'
}

export function isUnconfirmedWrite(error: unknown): boolean {
  return error instanceof TypeError || error instanceof SyntaxError ||
    (error instanceof PmoHttpError && (error.status === 408 || error.status >= 500 || error.code === 'REQUEST_IN_PROGRESS'))
}
