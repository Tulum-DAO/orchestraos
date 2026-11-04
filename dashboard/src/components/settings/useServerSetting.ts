/** One server row: save on change, shown at once; a refusal puts the old value back with the server's reason. */
import { useServerSettings } from '../../stores/serverSettings';
import { revertRow, saveServerSetting } from '../../lib/settingsApi';
import { useSavedFlash } from './settingsState';

export function useServerSetting(key: string) {
  const row = useServerSettings((s) => s.rows[key]);
  // The whole GET failed (offline, an older API): the row says why instead of showing "…" for ever.
  const loadError = useServerSettings((s) => (s.rows[key] ? null : s.error));
  const [status, flash] = useSavedFlash();
  /** `shown` is what the row shows until the server answers, when it differs from what is sent (the default model). */
  const save = async (value: unknown, shown: unknown = value) => {
    const st = useServerSettings.getState();
    const before = st.rows[key];
    if (!before) return;
    st.patch(key, { value: shown });
    const r = await saveServerSetting(key, value);
    if (r.ok) { useServerSettings.getState().put(r.row); flash({ kind: 'saved' }); }
    else {
      // Only while the row still shows this save's value: a change pushed meanwhile is newer and stays.
      const back = revertRow(useServerSettings.getState().rows[key], shown, before);
      if (back) useServerSettings.getState().put(back);
      flash({ kind: 'error', text: r.error });
    }
  };
  const locked = !!row && !row.editable && !row.readOnly;
  return { row, status, flash, save, locked, loadError };
}
