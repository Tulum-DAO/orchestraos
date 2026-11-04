/** Open Settings at a section over the current page (the operator menu, the shortcut, the agent panel's Server settings and provider rows). */
import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { nextSettingsState, openSettingsState, settingsPath, settingsSectionOf, settingsStateOf, type SectionId } from '../../lib/settingsRoute';

export function useOpenSettings(): (section?: SectionId | null) => void {
  const navigate = useNavigate();
  const location = useLocation();
  return useCallback((section: SectionId | null = 'general') => {
    const inSettings = settingsSectionOf(location.pathname) !== undefined;
    const state = inSettings ? nextSettingsState(settingsStateOf(location.state)) : openSettingsState(location);
    navigate(settingsPath(section), { state });
  }, [navigate, location]);
}
