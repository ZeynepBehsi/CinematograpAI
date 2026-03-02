import CytoscapeComponent from 'react-cytoscapejs'
import { useMemo } from 'react'

// ── Renk paleti ────────────────────────────────────────────────────────────────

const NODE_COLORS = {
  person:    '#e8c547',
  film:      '#ff6b35',
  genre:     '#6b8aff',
  studio:    '#47e8a0',
  country:   '#e84747',
  movement:  '#c547e8',
}

const LEGEND_LABELS = {
  person:   'Kişi',
  film:     'Film',
  genre:    'Tür',
  studio:   'Stüdyo',
  country:  'Ülke',
  movement: 'Akım',
}

// ── Cytoscape stil tanımı ──────────────────────────────────────────────────────

const STYLESHEET = [
  {
    selector: 'node',
    style: {
      'background-color':   'data(color)',
      'label':              'data(label)',
      'color':              '#e8e6e3',
      'font-size':          10,
      'font-family':        'JetBrains Mono, monospace',
      'text-outline-color': '#0a0a0f',
      'text-outline-width': 2,
      'width':              28,
      'height':             28,
      'text-valign':        'bottom',
      'text-halign':        'center',
      'text-margin-y':      5,
      'text-max-width':     '90px',
      'text-wrap':          'ellipsis',
    },
  },
  {
    selector: 'edge',
    style: {
      'width':                1.5,
      'line-color':           '#5a5a7a',
      'target-arrow-color':   '#5a5a7a',
      'target-arrow-shape':   'triangle',
      'arrow-scale':          1.2,
      'curve-style':          'bezier',
      'label':                'data(label)',
      'font-size':            8,
      'font-family':          'JetBrains Mono, monospace',
      'color':                '#8a8a9a',
      'text-background-color':   '#0a0a0f',
      'text-background-opacity': 0.75,
      'text-background-padding': '2px',
      'text-rotation':        'autorotate',
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-width': 2.5,
      'border-color': '#ffffff',
      'width':        36,
      'height':       36,
    },
  },
  {
    selector: 'node:hover',
    style: {
      'border-width': 1.5,
      'border-color': 'data(color)',
      'width':        32,
      'height':       32,
      'cursor':       'pointer',
    },
  },
]

const LAYOUT = {
  name:           'cose',
  animate:        true,
  animationDuration: 600,
  gravity:        0.25,
  nodeRepulsion:  () => 4500,
  idealEdgeLength: () => 80,
  edgeElasticity: () => 32,
  fit:            true,
  padding:        30,
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function GraphVisualization({ graphData, onNodeClick }) {
  if (!graphData?.nodes?.length) return null

  // graphData değiştiğinde Cytoscape'i destroy edip yeniden kur
  const graphKey = `${graphData.nodes.length}-${graphData.edges.length}`

  // Cytoscape element formatına dönüştür
  const elements = useMemo(() => [
    ...graphData.nodes.map(n => ({
      data: {
        id:    n.id,
        label: n.label,
        type:  n.type,
        color: NODE_COLORS[n.type?.toLowerCase()] ?? '#8a8a9a',
      },
    })),
    ...graphData.edges.map(e => ({
      data: {
        id:     e.id,
        source: e.source,
        target: e.target,
        label:  e.type ?? '',
      },
    })),
  ], [graphData])

  // Kaç farklı tip var? Legend için
  const presentTypes = [...new Set(graphData.nodes.map(n => n.type?.toLowerCase()).filter(Boolean))]

  function handleCy(cy) {
    cy.on('tap', 'node', evt => {
      const node = evt.target
      onNodeClick?.({
        id:    node.id(),
        label: node.data('label'),
        type:  node.data('type'),
      })
    })
  }

  return (
    <div className="relative overflow-hidden rounded-xl border border-cinema-border bg-cinema-bg">
      {/* Cytoscape canvas */}
      <CytoscapeComponent
        key={graphKey}
        elements={elements}
        stylesheet={STYLESHEET}
        layout={LAYOUT}
        cy={handleCy}
        style={{ width: '100%', height: '450px' }}
      />

      {/* Legend — sağ üst */}
      <div className="absolute right-3 top-3 rounded-lg border border-cinema-border bg-cinema-surface/90 px-3 py-2 backdrop-blur-sm">
        <p className="mb-1.5 font-mono text-[9px] uppercase tracking-widest text-cinema-muted">
          Node Tipleri
        </p>
        <div className="space-y-1">
          {presentTypes.map(type => (
            <div key={type} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: NODE_COLORS[type] ?? '#8a8a9a' }}
              />
              <span className="font-mono text-[10px] capitalize text-cinema-muted">
                {LEGEND_LABELS[type] ?? type}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Node / edge sayısı — sol alt */}
      <div className="absolute bottom-3 left-3 font-mono text-[10px] text-cinema-muted">
        {graphData.nodes.length} node · {graphData.edges.length} edge
      </div>
    </div>
  )
}
