import { buildInteractiveHtmlDocument } from '../tool-render/interactive-html-runtime';

declare global {
  interface Window {
    interactiveMessages: unknown[];
  }
}

window.interactiveMessages = [];
window.addEventListener('message', (event) => {
  window.interactiveMessages.push(event.data);
});

const agentHtml = `
  <form id="annotation-form">
    <div id="samples"></div>
    <button type="submit">Submit annotations</button>
  </form>
  <script>
    try { window.parent.document.body.dataset.compromised = 'yes' }
    catch { document.body.dataset.isolated = 'yes' }
    fetch('/mount/data/data_list.jsonl').then(r => r.text()).then(text => {
      const items = text.split('\\n').filter(Boolean).map(line => JSON.parse(line));
      document.querySelector('#samples').innerHTML = items.map(item =>
        '<article data-sample="' + item.id + '" style="background-image:url(' + item.background + ')">' +
          '<img src="' + item.path + '" alt="' + item.id + '">' +
          '<input name="' + item.id + '.label">' +
          '<textarea name="' + item.id + '.reason"></textarea>' +
        '</article>'
      ).join('');
      // An Agent script must not be able to continue HITL without a real user
      // submit gesture.
      document.querySelector('form').requestSubmit();
    });
  </script>
`;

const iframe = document.createElement('iframe');
iframe.id = 'artifact-frame';
iframe.setAttribute('sandbox', 'allow-scripts allow-forms');
iframe.srcdoc = buildInteractiveHtmlDocument({
  artifactId: 'ia_browser_test',
  html: agentHtml,
  resourceMounts: [
    {
      path_prefix: '/mount/',
      root_url: `${window.location.origin}/resources/mount-opaque/`,
    },
    {
      path_prefix: '/',
      root_url: `${window.location.origin}/resources/opaque/`,
    },
  ],
  baseUrl: `${window.location.origin}/resources/opaque/data/`,
  initialState: {
    schema_version: 1,
    fields: { 'sample-1.label': 'restored' },
  },
  frozen: false,
});
document.querySelector('#app')?.append(iframe);
