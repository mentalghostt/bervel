from test_api import client,auth,upload,PREFIX


def test_uploaded_file_has_pollable_index_status_and_scoped_resume(client):
    http,keys,_=client
    ref=upload(http,keys[0]).json()
    path=PREFIX+f"/artifacts/{ref['artifact_id']}/knowledge"
    result=http.get(path,headers=auth(keys[0]))
    assert result.status_code==200
    assert result.json()['state']=='pending'
    assert result.json()['completed_chunks']==0
    assert http.get(path,headers=auth(keys[1])).status_code==404
    assert http.post(path+'/resume',headers=auth(keys[2]),json={}).status_code==403
