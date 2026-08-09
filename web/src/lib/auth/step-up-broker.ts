export const STEP_UP_REQUEST_EVENT = 'vibecanvas:webauthn-step-up';

export interface StepUpRequestDetail {
  complete: (succeeded: boolean) => void;
}

let pending: Promise<boolean> | null = null;

export async function responseRequiresWebAuthnStepUp(
  response: Response,
): Promise<boolean> {
  if (response.status !== 403) return false;
  try {
    const payload = await response.clone().json() as {
      detail?: { code?: unknown; method?: unknown };
    };
    return payload.detail?.code === 'step_up_required'
      && payload.detail?.method === 'webauthn';
  } catch {
    return false;
  }
}

export function requestWebAuthnStepUp(): Promise<boolean> {
  if (pending) return pending;
  pending = new Promise<boolean>((resolve) => {
    let settled = false;
    const complete = (succeeded: boolean) => {
      if (settled) return;
      settled = true;
      resolve(succeeded);
    };
    if (typeof window === 'undefined') {
      complete(false);
      return;
    }
    const event = new CustomEvent<StepUpRequestDetail>(
      STEP_UP_REQUEST_EVENT,
      { detail: { complete }, cancelable: true },
    );
    const claimed = !window.dispatchEvent(event);
    if (!claimed) complete(false);
  }).finally(() => {
    pending = null;
  });
  return pending;
}
