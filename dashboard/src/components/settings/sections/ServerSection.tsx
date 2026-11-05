/** Settings › (Server) Time zone: the install's time zone (everyone, admin; a member sees it read-only with the lock).
 *  Its home until Server settings exists; the last category, under the install's label (lib/settingsRoute.ts). */
import { timeZoneOptions } from '../../../lib/settingsApi';
import { Group, PageIntro } from '../SettingsKit';
import { ServerSelectRow } from '../ServerRows';

export function ServerSection() {
  return (
    <>
      <PageIntro>Settings for the whole install. Only an admin can change them.</PageIntro>
      <Group title="Install" scope="everyone">
        <ServerSelectRow k="general.timeZone" label="Time zone (times shown in the app)" keywords={['time', 'zone', 'clock', 'timezone', 'server', 'install']}
          options={(row) => timeZoneOptions(typeof row.value === 'string' ? row.value : '')} />
      </Group>
    </>
  );
}
