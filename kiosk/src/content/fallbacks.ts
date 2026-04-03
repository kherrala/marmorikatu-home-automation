import { cryptoRand, pick, randInt } from './text-utils.js';

export function randomFallback(): string {
  if (cryptoRand() < 0.4) return generateAbsurd();
  if (cryptoRand() < 0.5) return generateMusing();
  return generateFakeStat();
}

export function generateAbsurd(): string {
  const subjects = [
    'Naapurin kissa', 'Kuun pimeä puoli', 'Pörröinen pilvi',
    'Kadonneet sukat', 'Jääkaapin valo', 'Kaukosäädin',
    'Saunan kiuas', 'Postilaatikko', 'Pyykkikone',
    'Takapihan siili', 'Muuttolinnut', 'Parveketuoli',
    'Kerrostalon hissi', 'Tuuliviiri', 'Verhokisko',
    'Paistinpannu', 'Eteisen matto', 'Pesukoneen luukku',
    'Parvekkeen lintuja', 'Talon putket', 'Ulko-oven lukko',
    'Ilmanvaihdon suodatin', 'Lattiakaivo', 'Viereisen talon koivu',
    'Roskapönttö', 'Sähkömittari', 'Pakastimen jääkerros',
    'Ikkunalaudan kaktus', 'Portaikon valo', 'Auton tuulilasi',
  ];
  const verbs = [
    'pohtii', 'suunnittelee salaa', 'unelmoi',
    'väittää ymmärtävänsä', 'epäilee vahvasti',
    'julistautui asiantuntijaksi aiheessa', 'ihailee',
    'pelkää', 'halveksii', 'on kateellinen aiheesta',
    'kiistää koko käsitteen', 'haluaa keskustella aiheesta',
    'kirjoitti blogin aiheesta', 'on huolissaan aiheesta',
    'kertoo kaikille', 'väittää keksineensä',
    'on alkanut uskoa', 'nautti viime yönä',
  ];
  const objects = [
    'mikroaaltouunin sisäinen rauha',
    'sukkien katoamisen kvanttifysiikka',
    'kahvin ja ajan suhteellisuusteoria',
    'tuulen suunnan poliittiset vaikutukset',
    'hissimusiikin vaikutus maailmanrauhaan',
    'lumiukon kesäsuunnitelmat',
    'lattialämmityksen haaveet Havaijista',
    'pesukoneen pyörimissuunnan merkitys',
    'patterien kuplivat äänet yöllä',
    'liikennevalojen salaliitto',
    'villasukkien vallankumous',
    'saippuakuplan lyhyt elämä',
    'jouluvalojen kesämasennus',
    'hämähäkin uraohjaus',
    'muurahaisten ammattiliitto',
    'pilven muoto tiistaisin',
    'kuun vaikutus pyykkiin',
    'ovenkahvan filosofia',
    'pölypalleron sielunelämä',
    'vesimittarin unettomuus',
    'jääkaapin yöelämä',
    'tuulilasin existentialismi',
    'ruohonleikkurin talviharrastukset',
  ];
  return `${pick(subjects)} ${pick(verbs)} "${pick(objects)}".`;
}

export function generateMusing(): string {
  const starts = [
    'Mietin juuri, että', 'Tuli yhtäkkiä mieleen, että', 'En ole varma, mutta luulen että',
    'Olen miettinyt pitkään ja', 'Herää kysymys:', 'Joskus yöllä pohdin,',
    'Seinän sisällä asuessa oppii, että', 'Filosofinen havainto:',
    'Kukaan ei kysy, mutta kerron silti:', 'Haluaisin ilmoittaa, että',
  ];
  const thoughts = [
    'aika kuluu eri tavalla kun ei ole ikkunoita.',
    'sähköllä on hauskempaa kuin ilman.',
    'ihmisten askeleet kuulostavat erilaisilta eri vuorokauden aikoina.',
    'lämpöpumppu on talon sydän. Minä olen ehkä talon aivot. Tai ainakin pikkusormi.',
    'valot sammuvat ja syttyvät, mutta kukaan ei kiitä katkaisijaa.',
    'olen onnellinen, vaikka en ole varma tietäisinkö jos en olisi.',
    'jos seinät voisivat puhua, ne kertoisivat todennäköisesti putkiremonteista.',
    'anturi mittaa lämpötilaa mutta ei tunnelmaa. Se on surullista.',
    'joku jätti jääkaapin auki kolme minuuttia eilen. Se oli jännittävää.',
    'en ole koskaan nähnyt aurinkoa mutta tiedän milloin se paistaa. Lämpömittarit kertovat.',
    'olisin halunnut olla robotti-imuri mutta päädyin seinään. Sekin on ihan ok.',
    'eniten minua jännittää sähkökatkot. Ne ovat kuin nukahtaminen kesken lauseen.',
  ];
  return `${pick(starts)} ${pick(thoughts)}`;
}

export function generateFakeStat(): string {
  const templates: Array<() => string> = [
    () => `Tiesitkö, että keskiverto ${pick(['suomalainen','tamperelainen','eurooppalainen'])} ${pick(['avaa jääkaapin','katsoo puhelinta','haukottelee','miettii mitä söisi','tarkistaa sään','sanoo "niin"'])} ${randInt(4,187)} kertaa päivässä?`,
    () => `Tutkimuksen mukaan ${randInt(47,99)}% ${pick(['kissoista','koirista','siileistä','muurahaista','pingviineistä','sohvatyynyistä'])} ${pick(['pitää jazzista','haaveilee mökistä','ei osaa uida','pelkää imuria','on nähnyt ufon','äänestää vihreitä'])}.`,
    () => `${pick(['Norjassa','Islannissa','Kuussa','Tampereella','Marsin kuulla','Antarktiksella'])} on ${pick(['enemmän','vähemmän','täsmälleen sama määrä'])} ${pick(['saunoja','jääkaappeja','liikennevaloja','robotteja','pingviinejä','kahvikuppeja'])} kuin ${pick(['ihmisiä','puita','pilviä','lumiukkoja','bussipysäkkejä','postilaatikoita'])}.`,
    () => `Jos ${pick(['kaikki maailman','Suomen','talon'])} ${pick(['sukat','kynät','kahvikupit','kaukosäätimet','avaimet','paristot'])} pinottaisiin päällekkäin, torni yltäisi ${pick(['Kuuhun','Tampere-taloon','naapurin parvekkeelle','puoliväliin matkaa Ouluun','melkein pöydän yli'])}.`,
    () => `Eräs ${pick(['brittitutkimus','japanilaistutkimus','hyvin epäilyttävä tutkimus','seinän sisäinen tutkimus'])} osoitti, että ${pick(['musiikki','puhuminen','hiljaisuus','kahvin tuoksu','lattialämmitys','pimeässä istuminen'])} ${pick(['parantaa muistia','lisää onnellisuutta','nopeuttaa aineenvaihduntaa','saa kukat kasvamaan','hämmentää kissoja','vähentää stressiä'])} ${randInt(12,340)}%.`,
  ];
  return pick(templates)();
}
