// Keep the public import used by existing callers. Fixed scrolling and implicit
// navigation of a caller's user tab have intentionally been removed.
import {captureRequestSpec} from './chrome_onboarding.mjs';
export {captureRequestSpec,captureOnboarding,CaptureError} from './chrome_onboarding.mjs';
export async function refreshRequestSpec(options){return captureRequestSpec(options);}
