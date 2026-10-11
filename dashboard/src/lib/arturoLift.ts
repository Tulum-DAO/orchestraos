/** How far the floating Arturo rises: above the tallest docked CLI (its top, measured from the app
 *  root's bottom), never below the bottom. */
export function arturoLift(hostBottom: number, avoidTops: number[]): number {
  let lift = 0;
  for (const top of avoidTops) lift = Math.max(lift, hostBottom - top);
  return Math.max(0, Math.round(lift));
}
