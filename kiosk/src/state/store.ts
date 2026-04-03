import { BehaviorSubject, Subject, type Observable } from 'rxjs';
import { scan, map, distinctUntilChanged, shareReplay } from 'rxjs/operators';
import type { AppState } from '../types/state.js';
import { type Action, reducer, INITIAL_STATE } from './machine.js';

export type { Action } from './machine.js';

const action$ = new Subject<Action>();

const stateSubject = new BehaviorSubject<AppState>(INITIAL_STATE);

export const state$: Observable<AppState> = action$.pipe(
  scan(reducer, INITIAL_STATE),
  shareReplay(1),
);

// Keep BehaviorSubject in sync for imperative getState()
state$.subscribe(stateSubject);

export function dispatch(action: Action): void {
  action$.next(action);
}

export function getState(): AppState {
  return stateSubject.getValue();
}

export function select<T>(selector: (s: AppState) => T): Observable<T> {
  return state$.pipe(map(selector), distinctUntilChanged());
}
