import { BehaviorSubject, Subject, type Observable } from 'rxjs';
import { map, distinctUntilChanged } from 'rxjs/operators';
import type { AppState } from '../types/state.js';
import { type Action, reducer, INITIAL_STATE } from './machine.js';

export type { Action } from './machine.js';

// BehaviorSubject always has a current value — eliminates the timing issue
// where withLatestFrom(state$) drops emissions before the first dispatch.
const stateSubject = new BehaviorSubject<AppState>(INITIAL_STATE);

export const state$: Observable<AppState> = stateSubject.asObservable();

export function dispatch(action: Action): void {
  const next = reducer(stateSubject.getValue(), action);
  stateSubject.next(next);
}

export function getState(): AppState {
  return stateSubject.getValue();
}

export function select<T>(selector: (s: AppState) => T): Observable<T> {
  return state$.pipe(map(selector), distinctUntilChanged());
}
