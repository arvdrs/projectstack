// JMeter's own Aggregate Report of a result file, saved as its "Save Table Data" does:
// the reference the aggregate report tests compare with.
//
//   java -cp "$JMETER_HOME/lib/*:$JMETER_HOME/lib/ext/*" -Djmeter.home=$JMETER_HOME -Djava.awt.headless=true \
//        groovy.ui.GroovyMain jmeter_aggregate.groovy results.jtl aggregate.csv [more.jtl more.csv …]
//
// The file is read by JMeter's ResultCollector (as File → Browse in the listener does), each sample
// goes to a SamplingStatCalculator per label and to the TOTAL one (as StatVisualizer.add does), and
// the table is written with the Aggregate Report's own columns, formatters and CSV writer.

import org.apache.jmeter.reporters.ResultCollector
import org.apache.jmeter.samplers.SampleResult
import org.apache.jmeter.save.CSVSaveService
import org.apache.jmeter.util.JMeterUtils
import org.apache.jmeter.visualizers.SamplingStatCalculator
import org.apache.jmeter.visualizers.StatGraphVisualizer
import org.apache.jmeter.visualizers.Visualizer

def home = System.getProperty('jmeter.home')
JMeterUtils.setJMeterHome(home)
JMeterUtils.loadJMeterProperties("${home}/bin/jmeter.properties")
JMeterUtils.initLocale()

args.toList().collate(2).each { pair -> aggregate(pair[0], pair[1]) }

def aggregate(String jtl, String out) {
    def rows = new LinkedHashMap<String, SamplingStatCalculator>()
    def total = new SamplingStatCalculator(JMeterUtils.getResString('aggregate_report_total_label'))
    def visualizer = [
        add    : { SampleResult r ->
            rows.computeIfAbsent(r.getSampleLabel(false), { new SamplingStatCalculator(it) }).addSample(r)
            total.addSample(r)
        },
        isStats: { false },
    ] as Visualizer

    def collector = new ResultCollector()
    collector.setFilename(jtl)
    collector.setListener(visualizer)
    collector.loadExistingFile()

    def model = StatGraphVisualizer.createObjectTableModel()
    rows.values().each { model.addRow(it) }
    model.addRow(total)
    def data = StatGraphVisualizer.getAllTableData(model, StatGraphVisualizer.getFormatters())
    def headers = StatGraphVisualizer.getLabels(StatGraphVisualizer.getColumns(), StatGraphVisualizer.getColumnsMsgParameters())
    new File(out).withWriter('UTF-8') { w -> CSVSaveService.saveCSVStats(data, w, headers) }
}
