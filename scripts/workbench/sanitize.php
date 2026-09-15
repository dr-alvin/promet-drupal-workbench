<?php
// Operates only inside generated isolated containers. Never connect to a source DB.
$db=new PDO('mysql:host=db;dbname=upgrade_test',getenv('MYSQL_USER'),getenv('MYSQL_PASSWORD'),[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$tables=$db->query('SHOW TABLES')->fetchAll(PDO::FETCH_COLUMN);
if(!in_array('users_field_data',$tables)||!in_array('config',$tables))throw new Exception('Expected an unprefixed Drupal 10 database; inspect unsupported schema');
$count=0;
function scrub($d,&$count){if(!is_array($d))return $d;foreach($d as $k=>&$v){if(is_array($v))$v=scrub($v,$count);elseif(is_string($v)&&preg_match('/^(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|consumer[_-]?key|client_secret|smtp_password|key_value)$/i',(string)$k)){$v='';$count++;}}return $d;}
$password=bin2hex(random_bytes(18));$hash=password_hash($password,PASSWORD_DEFAULT);
$db->beginTransaction();
$db->exec("UPDATE users_field_data SET name=CONCAT('test_user_',uid),mail=CONCAT('test_user_',uid,'@example.invalid'),init='',pass='',status=0,access=0,login=0 WHERE uid>0");
$q=$db->prepare("UPDATE users_field_data SET name='upgrade_test_admin',mail='upgrade_test_admin@example.invalid',pass=?,status=1 WHERE uid=1");$q->execute([$hash]);
foreach($tables as $t)if(preg_match('/^(cache_|webform_submission|sessions$|queue$|watchdog$|users_data$|provus_search_ai_logs$|provus_search_query$)/',$t))$db->exec('DELETE FROM `'.$t.'`');
$db->exec("DELETE FROM key_value WHERE collection='state'");
$u=$db->prepare('UPDATE config SET data=? WHERE collection=? AND name=?');
foreach($db->query('SELECT collection,name,data FROM config')->fetchAll(PDO::FETCH_ASSOC) as $r){$d=unserialize($r['data'],['allowed_classes'=>false]);if(!is_array($d))continue;$d=scrub($d,$count);if($r['name']==='system.site')$d['mail']='test@example.invalid';if($r['name']==='smtp.settings'){$d['smtp_on']=false;$d['smtp_host']='127.0.0.1';}if($r['name']==='automated_cron.settings')$d['interval']=0;if(str_starts_with($r['name'],'search_api.server.'))$d['status']=false;if(str_starts_with($r['name'],'webform.webform.'))$d['handlers']=[];$u->execute([serialize($d),$r['collection'],$r['name']]);}
$db->commit();
// Credentials are outside Drupal's public docroot and never returned in evidence.
file_put_contents('/tmp/d11-test-login.json',json_encode(['username'=>'upgrade_test_admin','password'=>$password]));chmod('/tmp/d11-test-login.json',0600);
echo json_encode(['accountsAnonymized'=>true,'syntheticUser'=>'upgrade_test_admin','secretValuesRemoved'=>$count,'integrationsDisabled'=>true,'privacyReviewComplete'=>false,'remainingReview'=>['Content and uploaded files','Custom user fields and integration-specific tables','Exported source configuration and custom code']]);
