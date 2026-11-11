from bervel.core.models import RegisterArtifact


def test_generated_offer_has_presales_owner_and_registration_replays(services):
    artifacts, _, _, actor = services
    command = RegisterArtifact(b'offer bytes', 'offer.txt', 'text/plain',
                               {'origin_system': 'bervel-presales'}, 'offer-once', owner_module_id='presales')
    first = artifacts.register(actor, command)
    assert artifacts.register(actor, command) == first
    assert artifacts.metadata(actor, first)['owner_module_id'] == 'presales'
