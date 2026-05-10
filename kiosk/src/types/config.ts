export interface SlideDefinition {
  readonly title: string;
  readonly src: string;
  // 'iframe' (default) — render `src` in an <iframe>.
  // 'native'  — render an empty <div id={nativeId}> instead, populated by
  //             whatever module owns that slide (e.g. announcer.ts for the
  //             announcement-history view).
  readonly kind?: 'iframe' | 'native';
  readonly nativeId?: string;
}
