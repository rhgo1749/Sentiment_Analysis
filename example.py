import kobert.pytorch_kobert
import kobert.utils
import loader
import model as md

import gluonnlp as nlp
import torch
import transformers
import numpy as np
import random

example_model_path = "example_model.pt"


def ex__training(opt=md.DEFAULT_OPTION, ctx="cuda:0"):
    device = torch.device(ctx)

    # load bert model
    bert_model, vocab = kobert.pytorch_kobert.get_pytorch_kobert_model()

    # load train / test dataset
    bert_tokenizer = md.get_bert_tokenizer(vocab)

    train_data_path, test_data_path = loader.download_corpus_data()  # Naver sentiment movie corpus v1.0
    dataset_train = md.get_bert_dataset(train_data_path, sentence_idx=1, label_idx=2,
                                        max_len=opt["max_len"], bert_tokenizer=bert_tokenizer)
    dataset_test = md.get_bert_dataset(test_data_path, sentence_idx=1, label_idx=2,
                                       max_len=opt["max_len"], bert_tokenizer=bert_tokenizer)

    # data loader
    train_dataloader = torch.utils.data.DataLoader(dataset_train, batch_size=opt["batch_size"], num_workers=0)
    test_dataloader = torch.utils.data.DataLoader(dataset_test, batch_size=opt["batch_size"], num_workers=0)

    # model
    model = md.BERTClassifier(bert_model, dr_rate=opt["drop_out_rate"]).to(device)

    no_decay = ['bias', 'LayerNorm.weight']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         'weight_decay': 0.01},
        {'params': [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    ]
    optimizer = transformers.AdamW(optimizer_grouped_parameters, lr=opt["learning_rate"])
    loss_function = torch.nn.CrossEntropyLoss()

    t_total = len(train_dataloader) * opt["num_epochs"]
    warmup_steps = int(t_total * opt["warmup_ratio"])
    scheduler = transformers.optimization.get_linear_schedule_with_warmup(optimizer, warmup_steps, t_total)

    for e in range(opt["num_epochs"]):
        train_accuracy = 0.0
        test_accuracy = 0.0

        # Train Batch
        model.train()
        for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(train_dataloader):
            optimizer.zero_grad()

            # set train batch
            token_ids = token_ids.long().to(device)
            segment_ids = segment_ids.long().to(device)
            valid_length = valid_length
            label = label.long().to(device)

            # get word embedding
            attention_mask = md.gen_attention_mask(token_ids, valid_length)
            word_embedding = model.bert.get_input_embeddings()
            x = word_embedding(token_ids)

            # forward propagation
            out = model(x, segment_ids, attention_mask)

            # backward propagation
            x.retain_grad()
            loss = loss_function(out, label)
            loss.backward()

            # optimization
            torch.nn.utils.clip_grad_norm_(model.parameters(), opt["max_grad_norm"])
            optimizer.step()
            scheduler.step()  # Update learning rate schedule
            train_accuracy += md.calculate_accuracy(out, label)

            if batch_id % opt["log_interval"] == 0:
                print("epoch {} batch id {} loss {} train accuracy {}".format(e + 1, batch_id + 1,
                                                                              loss.data.cpu().numpy(),
                                                                              train_accuracy / (batch_id + 1)))
        print("epoch {} train accuracy {}".format(e + 1, train_accuracy / (batch_id + 1)))

        # Test Batch
        model.eval()
        with torch.no_grad():
            for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(test_dataloader):
                # set test batch
                token_ids = token_ids.long().to(device)
                segment_ids = segment_ids.long().to(device)
                valid_length = valid_length
                label = label.long().to(device)

                # get word embedding
                attention_mask = md.gen_attention_mask(token_ids, valid_length)
                word_embedding = model.bert.get_input_embeddings()
                x = word_embedding(token_ids)

                # forward propagation
                out = model(x, segment_ids, attention_mask)

                # test accuracy
                test_accuracy += md.calculate_accuracy(out, label)
            print("epoch {} test accuracy {}".format(e + 1, test_accuracy / (batch_id + 1)))

        torch.save(model.state_dict(), example_model_path)


def ex__sentiment_analysis():
    _, corpus_path = loader.download_corpus_data()  # Naver sentiment movie corpus v1.0
    result, accuracy = md.sentiment_analysis(example_model_path, corpus_path, sentence_idx=1, label_idx=2, show=True)
    print(f"Sentiment Analysis Accuracy: {'%0.2f' % accuracy * 100}%")

    dataset = nlp.data.TSVDataset(corpus_path, field_indices=[1, 2], num_discard_samples=1)
    corpus_list = []
    label_list = []
    for data in dataset:
        corpus_list.append(f"{data[1]}: " + data[0])
        label_list.append(data[1])

    label = np.array(label_list, dtype=np.int)
    result = result[:, 1]

    for i in range(0, len(corpus_list)):
        corpus_list[i] = f"{'%0.2f' % result[i]} / " + corpus_list[i]

    result = (result + 0.5).astype(np.int)
    corpus = np.array(corpus_list, dtype=np.str)
    np.savetxt("sa_incorrect_case.txt", corpus[result != label], fmt="%s", delimiter=" ", encoding='UTF-8')
    np.savetxt("sa_correct_case.txt", corpus[result == label], fmt="%s", delimiter=" ", encoding='UTF-8')


def ex__ABSA_training(opt=md.DEFAULT_OPTION, ctx="cuda:0"):
    device = torch.device(ctx)

    # load dataset
    dataset = nlp.data.TSVDataset("sentiment_dataset.csv", field_indices=[0, 1, 3], num_discard_samples=1)
    object_text_0 = "대상"
    object_text_1 = "측면"

    # random value
    rnd_0 = np.random.uniform(0, 1, len(dataset)) > 0.5
    rnd_1 = np.random.randint(0, len(dataset) - 1, len(dataset))
    rnd_2 = np.random.uniform(0, 1, len(dataset)) > 0.5

    # split by list
    data_list = []
    for idx, (corpus, aspect, label) in enumerate(list(dataset)):
        # original data
        data_list.append([corpus, [0.5, 0.5, 0.5, 0.5]])

        # augmented data - single
        label_number = 1.0 if label == "positive" else 0.0
        if rnd_0[idx]:
            aug_corpus = corpus.replace(aspect, object_text_0)
            aug_label = [1 - label_number, label_number, 0.5, 0.5]
        else:
            aug_corpus = corpus.replace(aspect, object_text_1)
            aug_label = [0.5, 0.5, 1 - label_number, label_number]
        data_list.append([aug_corpus, aug_label])

        # augmented data - double
        rnd_1[idx] = len(dataset) - 1 if rnd_1[idx] == idx else rnd_1[idx]
        corpus_1, aspect_1, label_1 = dataset[rnd_1[idx]]
        label_number_1 = 1.0 if label_1 == "positive" else 0.0

        if rnd_2[idx]:
            aug_text_0 = object_text_0
            aug_text_1 = object_text_1
        else:
            aug_text_0 = object_text_1
            aug_text_1 = object_text_0

        aug_corpus = corpus.replace(aspect, aug_text_0) + " " + corpus_1.replace(aspect_1, aug_text_1)
        aug_label = [1 - label_number, label_number, 1 - label_number_1, label_number_1]
        data_list.append([aug_corpus, aug_label])

    # random shuffle
    random.shuffle(data_list)

    # load bert model
    bert_model, vocab = kobert.pytorch_kobert.get_pytorch_kobert_model()

    # load train / test dataset
    test_count = int(len(data_list) * 0.2)
    bert_tokenizer = md.get_bert_tokenizer(vocab)
    dataset_train = md.BERTDataset(data_list[test_count:], 0, 1, bert_tokenizer, opt["max_len"], pad=True, pair=False)
    dataset_test = md.BERTDataset(data_list[:test_count], 0, 1, bert_tokenizer, opt["max_len"], pad=True, pair=False)

    # data loader
    train_dataloader = torch.utils.data.DataLoader(dataset_train, batch_size=opt["batch_size"], num_workers=0)
    test_dataloader = torch.utils.data.DataLoader(dataset_test, batch_size=opt["batch_size"], num_workers=0)

    # aspect-base sentiment analysis model
    sa_model = md.BERTClassifier(bert_model, dr_rate=opt["drop_out_rate"]).to(device)
    sa_model.load_state_dict(torch.load(example_model_path))
    model = md.ABSAClassifier(sa_model.bert, sa_model.classifier,
                              dr_rate_0=opt["drop_out_rate"], dr_rate_1=0.5).to(device)

    no_decay = ['bias', 'LayerNorm.weight']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         'weight_decay': 0.01},
        {'params': [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    ]
    optimizer = transformers.AdamW(optimizer_grouped_parameters, lr=opt["learning_rate"])
    loss_function = md.softmax_cross_entropy_loss

    t_total = len(train_dataloader) * opt["num_epochs"]
    warmup_steps = int(t_total * opt["warmup_ratio"])
    scheduler = transformers.optimization.get_linear_schedule_with_warmup(optimizer, warmup_steps, t_total)

    for e in range(opt["num_epochs"]):
        train_accuracy = 0.0
        test_accuracy = 0.0

        # Train Batch
        model.train()
        for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(train_dataloader):
            optimizer.zero_grad()

            # set train batch
            token_ids = token_ids.long().to(device)
            segment_ids = segment_ids.long().to(device)
            valid_length = valid_length
            label = label.float().to(device)

            # get word embedding
            attention_mask = md.gen_attention_mask(token_ids, valid_length)
            word_embedding = model.bert.get_input_embeddings()
            x = word_embedding(token_ids)

            # forward propagation
            _, out_1, out_2 = model(x, segment_ids, attention_mask, sa=False, absa=True)

            # backward propagation
            x.retain_grad()
            loss = loss_function(out_1, label[:, 0:2]) + loss_function(out_2, label[:, 2:4])
            loss.backward()

            # optimization
            torch.nn.utils.clip_grad_norm_(model.parameters(), opt["max_grad_norm"])
            optimizer.step()
            scheduler.step()  # Update learning rate schedule

            train_accuracy += md.calculate_accuracy_absa(torch.cat((out_1, out_2), dim=1), label)

            if batch_id % opt["log_interval"] == 0:
                print("epoch {} batch id {} loss {} train accuracy {}".format(e + 1, batch_id + 1,
                                                                              loss.data.cpu().numpy(),
                                                                              train_accuracy / (batch_id + 1)))
        print("epoch {} train accuracy {}".format(e + 1, train_accuracy / (batch_id + 1)))

        # Test Batch
        model.eval()
        with torch.no_grad():
            for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(test_dataloader):
                # set test batch
                token_ids = token_ids.long().to(device)
                segment_ids = segment_ids.long().to(device)
                valid_length = valid_length
                label = label.long().to(device)

                # get word embedding
                attention_mask = md.gen_attention_mask(token_ids, valid_length)
                word_embedding = model.bert.get_input_embeddings()
                x = word_embedding(token_ids)

                # forward propagation
                out = model(x, segment_ids, attention_mask)

                # test accuracy
                test_accuracy += md.calculate_accuracy(out, label)
            print("epoch {} test accuracy {}".format(e + 1, test_accuracy / (batch_id + 1)))

        torch.save(model.state_dict(), "ABSA_model.pt")


if __name__ == '__main__':
    # ex__training()
    # ex__sentiment_analysis()
    ex__ABSA_training()



