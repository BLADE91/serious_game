"""Refresh derived references after editorial changes, preserving route expectations.

This does not mark any route tested or regenerate story text. The V3 JSON files
are the maintained source; the historical M2 builder targets a different package.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from serious_game_backend.infrastructure.script_packages.file_loader import FileScriptPackageLoader

def main():
    package = ROOT / 'content/packages/pkg_gameplay_v3'
    def read(name): return json.loads((package / name).read_text(encoding='utf-8'))
    def write(name, value): (package / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    beats = {item['story_day']: item for item in read('story_beats.json')['beats']}
    decisions = {item['decision_id']: item for item in read('decisions.json')['decisions']}
    profiles_doc = read('npc_profiles.json')
    profiles = profiles_doc.get('npcs', profiles_doc.get('profiles', profiles_doc.get('npc_profiles', [])))
    assert profiles, 'NPC source format changed; inspect before updating references'
    matrix = read('story_acceptance_matrix.json')
    for row in matrix['days']:
        beat = beats[row['story_day']]
        blocks = beat['opening_blocks']
        ds = [decisions[id] for id in row['decision_ids']]
        row['opening_block_ids'] = [b['block_id'] for b in blocks]
        row['required_story_entry_ids'] = row['opening_block_ids'][:]
        row['prerequisite_narrative_ids'] = [b['block_id'] for d in ds for b in d['presentation_blocks']]
        row['decision_display_node_ids'] = row['prerequisite_narrative_ids'][:]
        row['decision_presentation_order'] = [{'decision_id': d['decision_id'], 'presentation_entry_ids': [b['block_id'] for b in d['presentation_blocks']]} for d in ds]
        row['outcome_transition_ids'] = [b['block_id'] for d in ds for b in d['followup_blocks']]
        row['scene_ids'] = list(dict.fromkeys(b['scene_id'] for b in [*blocks, *(b for d in ds for b in d['presentation_blocks'] + d['followup_blocks'])] if b.get('scene_id')))
        row['visible_speakers'] = list(dict.fromkeys(b['speaker'] for b in blocks if b.get('speaker')))
        text = '\n'.join((b.get('speaker', '') + '\n' + b['text']) for b in blocks)
        introduced = {p['npc_id'] for p in profiles if p['name'] in text}
        old_order = [id for id in row['introduced_npc_ids'] if id in introduced]
        row['introduced_npc_ids'] = old_order + sorted(introduced - set(old_order))
        byname = {p['name']: p['npc_id'] for p in profiles}
        contactable = [t for t in row['npc_discovery_transitions'] if t['state'] == 'contactable']
        row['npc_discovery_transitions'] = ([{'npc_id': id, 'state': 'mentioned'} for id in row['introduced_npc_ids']]
            + [{'npc_id': byname[name], 'state': 'encountered'} for name in row['visible_speakers']]
            + contactable)
    write('story_acceptance_matrix.json', matrix)
    manifest = read('package_manifest.json')
    manifest['content_hash'] = FileScriptPackageLoader.compute_content_hash(package)
    write('package_manifest.json', manifest)
    FileScriptPackageLoader().load(package)
    print('Editorial references and content hash validated:', manifest['content_hash'])

if __name__ == '__main__': main()
